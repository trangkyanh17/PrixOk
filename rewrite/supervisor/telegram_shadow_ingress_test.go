package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestValidateTelegramShadowAddrLoopbackOnly(t *testing.T) {
	for _, addr := range []string{"127.0.0.1:18750", "localhost:18750", "[::1]:18750"} {
		if err := validateTelegramShadowAddr(addr); err != nil {
			t.Fatalf("addr=%s err=%v", addr, err)
		}
	}
	for _, addr := range []string{"0.0.0.0:18750", "192.168.1.5:18750", ":18750", "bad"} {
		if err := validateTelegramShadowAddr(addr); err == nil {
			t.Fatalf("addr=%s should be rejected", addr)
		}
	}
}

func TestTelegramShadowHandlerAcceptsMetadataWithoutOutboundSideEffects(t *testing.T) {
	ingress := newTelegramShadowIngress("127.0.0.1:18750", "")
	payload := telegramShadowEvent{
		Version:   telegramShadowSchemaVersion,
		Kind:      "message",
		ChatID:    -100123,
		MessageID: 42,
		UserID:    7,
		ChatType:  "supergroup",
		Text:      "hello shadow",
		Command:   "",
		Media: &telegramShadowMedia{
			Type:     "sticker",
			FileID:   "file-id",
			UniqueID: "unique-id",
			Emoji:    "🙂",
		},
	}
	body, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/telegram/shadow", bytes.NewReader(body))
	response := httptest.NewRecorder()
	ingress.handler().ServeHTTP(response, request)
	if response.Code != http.StatusAccepted {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	if ingress.accepted.Load() != 1 || ingress.rejected.Load() != 0 {
		t.Fatalf("accepted=%d rejected=%d", ingress.accepted.Load(), ingress.rejected.Load())
	}
}

func TestTelegramShadowHandlerRejectsUnknownFieldsAndWrongSchema(t *testing.T) {
	ingress := newTelegramShadowIngress("127.0.0.1:18750", "")
	for _, raw := range []string{
		`{"version":1,"kind":"message","message_id":1,"unexpected":true}`,
		`{"version":99,"kind":"message","message_id":1}`,
		`{"version":1,"kind":"message","message_id":0}`,
		`{"version":1,"kind":"inline_query"}`,
	} {
		request := httptest.NewRequest(http.MethodPost, "/v1/telegram/shadow", bytes.NewBufferString(raw))
		response := httptest.NewRecorder()
		ingress.handler().ServeHTTP(response, request)
		if response.Code != http.StatusBadRequest {
			t.Fatalf("raw=%s status=%d", raw, response.Code)
		}
	}
	if ingress.rejected.Load() != 4 {
		t.Fatalf("rejected=%d", ingress.rejected.Load())
	}
}

func TestTelegramShadowHandlerSecret(t *testing.T) {
	ingress := newTelegramShadowIngress("127.0.0.1:18750", "secret")
	body := bytes.NewBufferString(`{"version":1,"kind":"callback_query"}`)
	request := httptest.NewRequest(http.MethodPost, "/v1/telegram/shadow", body)
	response := httptest.NewRecorder()
	ingress.handler().ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d", response.Code)
	}

	body = bytes.NewBufferString(`{"version":1,"kind":"callback_query"}`)
	request = httptest.NewRequest(http.MethodPost, "/v1/telegram/shadow", body)
	request.Header.Set("X-Atri-Shadow-Secret", "secret")
	response = httptest.NewRecorder()
	ingress.handler().ServeHTTP(response, request)
	if response.Code != http.StatusAccepted {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
}

func TestTelegramShadowHealth(t *testing.T) {
	ingress := newTelegramShadowIngress("127.0.0.1:18750", "")
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	response := httptest.NewRecorder()
	ingress.handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d", response.Code)
	}
}

func TestRunTelegramShadowIngressStopsWithContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	cfg := config{
		TelegramShadowAddr:  "127.0.0.1:0",
		TelegramShadowRetry: time.Millisecond,
	}
	if err := runTelegramShadowIngress(ctx, cfg); err == nil {
		t.Fatal("expected context cancellation")
	}
}
