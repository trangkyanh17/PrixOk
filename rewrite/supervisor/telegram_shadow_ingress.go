package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"sync/atomic"
	"time"
)

const (
	telegramShadowSchemaVersion = 1
	telegramShadowMaxBodyBytes  = int64(256 * 1024)
	atriParityMaxBodyBytes      = int64(64 * 1024)
)

type telegramShadowMedia struct {
	Type       string `json:"type,omitempty"`
	FileID     string `json:"file_id,omitempty"`
	UniqueID   string `json:"unique_id,omitempty"`
	FileName   string `json:"file_name,omitempty"`
	MIMEType   string `json:"mime_type,omitempty"`
	Size       int64  `json:"size,omitempty"`
	Width      int    `json:"width,omitempty"`
	Height     int    `json:"height,omitempty"`
	Duration   int    `json:"duration,omitempty"`
	Emoji      string `json:"emoji,omitempty"`
	IsAnimated bool   `json:"is_animated,omitempty"`
	IsVideo    bool   `json:"is_video,omitempty"`
}

type telegramShadowEvent struct {
	Version      int                  `json:"version"`
	Kind         string               `json:"kind"`
	ChatID       int64                `json:"chat_id,omitempty"`
	MessageID    int64                `json:"message_id,omitempty"`
	ThreadID     int64                `json:"thread_id,omitempty"`
	UserID       int64                `json:"user_id,omitempty"`
	ChatType     string               `json:"chat_type,omitempty"`
	Text         string               `json:"text,omitempty"`
	Command      string               `json:"command,omitempty"`
	CallbackData string               `json:"callback_data,omitempty"`
	Media        *telegramShadowMedia `json:"media,omitempty"`
}

type telegramShadowIngress struct {
	addr     string
	secret   string
	accepted atomic.Uint64
	rejected atomic.Uint64
	parity   *atriParityEngine
}

func newTelegramShadowIngress(addr, secret string) *telegramShadowIngress {
	return &telegramShadowIngress{
		addr:   strings.TrimSpace(addr),
		secret: strings.TrimSpace(secret),
		parity: newAtriParityEngine(),
	}
}

func validateTelegramShadowAddr(addr string) error {
	host, _, err := net.SplitHostPort(strings.TrimSpace(addr))
	if err != nil {
		return fmt.Errorf("invalid Telegram shadow listen address: %w", err)
	}
	host = strings.Trim(host, "[]")
	if strings.EqualFold(host, "localhost") {
		return nil
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return fmt.Errorf("Telegram shadow ingress must bind to loopback, got %q", host)
	}
	return nil
}

func validTelegramShadowKind(kind string) bool {
	switch kind {
	case "message", "edited_message", "callback_query":
		return true
	default:
		return false
	}
}

func validateTelegramShadowEvent(event telegramShadowEvent) error {
	if event.Version != telegramShadowSchemaVersion {
		return fmt.Errorf("unsupported Telegram shadow schema version %d", event.Version)
	}
	if !validTelegramShadowKind(event.Kind) {
		return fmt.Errorf("unsupported Telegram shadow event kind %q", event.Kind)
	}
	if event.Kind != "callback_query" && event.MessageID <= 0 {
		return errors.New("message_id is required for message events")
	}
	return nil
}

func (ingress *telegramShadowIngress) authorized(request *http.Request) bool {
	if ingress == nil || ingress.secret == "" {
		return true
	}
	provided := request.Header.Get("X-Atri-Shadow-Secret")
	if len(provided) != len(ingress.secret) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(provided), []byte(ingress.secret)) == 1
}

func (ingress *telegramShadowIngress) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet {
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		writer.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"status":   "ok",
			"mode":     "shadow",
			"accepted": ingress.accepted.Load(),
			"rejected": ingress.rejected.Load(),
			"parity":   ingress.parity.snapshot(),
		})
	})

	mux.HandleFunc("/v1/atri/parity", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			ingress.parity.rejected.Add(1)
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if !ingress.authorized(request) {
			ingress.parity.rejected.Add(1)
			writer.WriteHeader(http.StatusUnauthorized)
			return
		}

		request.Body = http.MaxBytesReader(writer, request.Body, atriParityMaxBodyBytes)
		defer request.Body.Close()
		decoder := json.NewDecoder(request.Body)
		decoder.DisallowUnknownFields()
		var event atriParityEvent
		if err := decoder.Decode(&event); err != nil {
			ingress.parity.rejected.Add(1)
			http.Error(writer, "invalid Atri parity event", http.StatusBadRequest)
			return
		}

		match, reason, err := ingress.parity.evaluate(event)
		if err != nil {
			http.Error(writer, err.Error(), http.StatusBadRequest)
			return
		}

		// Never log route text, Telegram identifiers, provider credentials,
		// model output, tool arguments, or tool results. This is decision parity only.
		log.Printf(
			"ATRI_PARITY stage=%s match=%t reason=%s mode=%s tool=%s",
			event.Stage,
			match,
			reason,
			strings.ToLower(strings.TrimSpace(event.Mode+event.ActualMode)),
			event.ToolName,
		)
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusAccepted)
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"accepted": true,
			"mode":     "decision-shadow",
			"match":    match,
			"reason":   reason,
		})
	})

	mux.HandleFunc("/v1/telegram/shadow", func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost {
			ingress.rejected.Add(1)
			writer.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if !ingress.authorized(request) {
			ingress.rejected.Add(1)
			writer.WriteHeader(http.StatusUnauthorized)
			return
		}

		request.Body = http.MaxBytesReader(writer, request.Body, telegramShadowMaxBodyBytes)
		defer request.Body.Close()
		decoder := json.NewDecoder(request.Body)
		decoder.DisallowUnknownFields()
		var event telegramShadowEvent
		if err := decoder.Decode(&event); err != nil {
			ingress.rejected.Add(1)
			if errors.Is(err, io.EOF) {
				http.Error(writer, "empty request body", http.StatusBadRequest)
				return
			}
			http.Error(writer, "invalid Telegram shadow event", http.StatusBadRequest)
			return
		}
		if err := validateTelegramShadowEvent(event); err != nil {
			ingress.rejected.Add(1)
			http.Error(writer, err.Error(), http.StatusBadRequest)
			return
		}

		ingress.accepted.Add(1)
		mediaType := ""
		if event.Media != nil {
			mediaType = event.Media.Type
		}
		// Never log message text, callback payload, or Telegram identifiers.
		// Shadow ingress is local parity observation, not a second transcript.
		log.Printf(
			"TELEGRAM_SHADOW_ACCEPT kind=%s chat_type=%s command=%s media=%s",
			event.Kind,
			event.ChatType,
			event.Command,
			mediaType,
		)
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusAccepted)
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"accepted": true,
			"mode":     "shadow",
		})
	})
	return mux
}

func (ingress *telegramShadowIngress) run(ctx context.Context) error {
	if ingress == nil {
		return errors.New("Telegram shadow ingress is nil")
	}
	if err := validateTelegramShadowAddr(ingress.addr); err != nil {
		return err
	}
	listener, err := net.Listen("tcp", ingress.addr)
	if err != nil {
		return err
	}
	server := &http.Server{
		Handler:           ingress.handler(),
		ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout:       5 * time.Second,
		WriteTimeout:      5 * time.Second,
		IdleTimeout:       30 * time.Second,
	}

	result := make(chan error, 1)
	go func() {
		err := server.Serve(listener)
		if errors.Is(err, http.ErrServerClosed) {
			err = nil
		}
		result <- err
	}()

	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdownCtx)
		<-result
		return ctx.Err()
	case err := <-result:
		return err
	}
}

func runTelegramShadowIngress(ctx context.Context, cfg config) error {
	retry := cfg.TelegramShadowRetry
	if retry <= 0 {
		retry = 15 * time.Second
	}
	for {
		ingress := newTelegramShadowIngress(cfg.TelegramShadowAddr, cfg.TelegramShadowSecret)
		log.Printf("TELEGRAM_SHADOW_START addr=%s mode=observe-only", cfg.TelegramShadowAddr)
		err := ingress.run(ctx)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		log.Printf("TELEGRAM_SHADOW_RETRY error=%v retry=%s", err, retry)
		timer := time.NewTimer(retry)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}
