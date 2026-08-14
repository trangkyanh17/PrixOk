package runtimecfg

import (
	"context"
	"fmt"
	"strings"
	"sync"
)

type OrchestratorRunner interface {
	Run(context.Context, OrchestratorRequest) (OrchestratorResult, error)
}

type TelegramGateway interface {
	ReplyText(
		context.Context,
		int64,
		int64,
		int64,
		string,
	) (int64, error)
	EditText(context.Context, int64, int64, string) error
	ReplyVoice(
		context.Context,
		int64,
		int64,
		int64,
		[]byte,
		string,
	) error
}

type TelegramIncomingMessage struct {
	ChatID             int64
	MessageID          int64
	ThreadID           int64
	UserID             int64
	Text               string
	AttachmentBytes    []byte
	AttachmentMIMEType string
}

type TelegramAtriRuntime struct {
	Runner  OrchestratorRunner
	Gateway TelegramGateway
}

type telegramProgressResponder struct {
	gateway   TelegramGateway
	chatID    int64
	replyTo   int64
	threadID  int64
	mu        sync.Mutex
	messageID int64
	lastText  string
}

func firstTelegramChunk(text string) string {
	text = CleanPublicAnswer(text)
	chunks := SplitReplyChunks(text)
	if len(chunks) == 0 {
		return ""
	}
	return chunks[0]
}

func (responder *telegramProgressResponder) Callback(
	ctx context.Context,
	stage int,
	text string,
) error {
	if responder == nil || responder.gateway == nil || stage != 1 {
		return nil
	}
	first := firstTelegramChunk(text)
	if first == "" {
		return nil
	}

	responder.mu.Lock()
	defer responder.mu.Unlock()

	if responder.messageID == 0 {
		messageID, err := responder.gateway.ReplyText(
			ctx,
			responder.chatID,
			responder.replyTo,
			responder.threadID,
			first,
		)
		if err != nil {
			return err
		}
		responder.messageID = messageID
		responder.lastText = first
		return nil
	}

	if first == responder.lastText {
		return nil
	}
	if err := responder.gateway.EditText(
		ctx,
		responder.chatID,
		responder.messageID,
		first,
	); err != nil {
		return err
	}
	responder.lastText = first
	return nil
}

func (responder *telegramProgressResponder) Finalize(
	ctx context.Context,
	chunks []string,
) error {
	if responder == nil || responder.gateway == nil || len(chunks) == 0 {
		return nil
	}

	first := strings.TrimSpace(chunks[0])
	if first == "" {
		return nil
	}

	responder.mu.Lock()
	messageID := responder.messageID
	lastText := responder.lastText
	if messageID == 0 {
		createdID, err := responder.gateway.ReplyText(
			ctx,
			responder.chatID,
			responder.replyTo,
			responder.threadID,
			first,
		)
		if err != nil {
			responder.mu.Unlock()
			return err
		}
		responder.messageID = createdID
		responder.lastText = first
	} else if first != lastText {
		if err := responder.gateway.EditText(
			ctx,
			responder.chatID,
			messageID,
			first,
		); err != nil {
			createdID, fallbackErr := responder.gateway.ReplyText(
				ctx,
				responder.chatID,
				responder.replyTo,
				responder.threadID,
				first,
			)
			if fallbackErr != nil {
				responder.mu.Unlock()
				return fmt.Errorf(
					"telegram final edit failed: %v; fallback reply failed: %w",
					err,
					fallbackErr,
				)
			}
			responder.messageID = createdID
		}
		responder.lastText = first
	}
	responder.mu.Unlock()

	for _, chunk := range chunks[1:] {
		chunk = strings.TrimSpace(chunk)
		if chunk == "" {
			continue
		}
		if _, err := responder.gateway.ReplyText(
			ctx,
			responder.chatID,
			responder.replyTo,
			responder.threadID,
			chunk,
		); err != nil {
			return err
		}
	}
	return nil
}

func buildTelegramToolContext(
	incoming TelegramIncomingMessage,
	base ToolContext,
	gateway TelegramGateway,
) ToolContext {
	base.UserID = incoming.UserID
	base.ChatID = incoming.ChatID
	base.ThreadID = incoming.ThreadID
	metadata := cloneAnyMap(base.Metadata)
	if metadata == nil {
		metadata = map[string]any{}
	}
	if len(incoming.AttachmentBytes) > 0 {
		metadata["attachment_bytes"] = append(
			[]byte(nil),
			incoming.AttachmentBytes...,
		)
		metadata["attachment_mime_type"] = strings.TrimSpace(
			incoming.AttachmentMIMEType,
		)
	}
	if gateway != nil {
		metadata["voice_sender"] = GoogleVoiceSender(
			func(
				ctx context.Context,
				_ ToolContext,
				audio []byte,
				filename string,
			) error {
				return gateway.ReplyVoice(
					ctx,
					incoming.ChatID,
					incoming.MessageID,
					incoming.ThreadID,
					audio,
					filename,
				)
			},
		)
	}
	base.Metadata = metadata
	return base
}

func (runtime *TelegramAtriRuntime) Handle(
	ctx context.Context,
	incoming TelegramIncomingMessage,
	request OrchestratorRequest,
) (OrchestratorResult, error) {
	if runtime == nil || runtime.Runner == nil {
		return OrchestratorResult{}, fmt.Errorf("orchestrator runner is required")
	}
	if runtime.Gateway == nil {
		return OrchestratorResult{}, fmt.Errorf("telegram gateway is required")
	}

	if strings.TrimSpace(request.PublicText) == "" {
		request.PublicText = strings.TrimSpace(incoming.Text)
	}
	request.ToolContext = buildTelegramToolContext(
		incoming,
		request.ToolContext,
		runtime.Gateway,
	)

	responder := &telegramProgressResponder{
		gateway:  runtime.Gateway,
		chatID:   incoming.ChatID,
		replyTo:  incoming.MessageID,
		threadID: incoming.ThreadID,
	}
	request.ProgressCallback = func(stage int, text string) error {
		return responder.Callback(ctx, stage, text)
	}

	result, err := runtime.Runner.Run(ctx, request)
	if err != nil {
		return OrchestratorResult{}, err
	}
	chunks := result.Chunks
	if len(chunks) == 0 && strings.TrimSpace(result.Text) != "" {
		chunks = SplitReplyChunks(CleanPublicAnswer(result.Text))
	}
	if err := responder.Finalize(ctx, chunks); err != nil {
		return result, err
	}
	return result, nil
}
