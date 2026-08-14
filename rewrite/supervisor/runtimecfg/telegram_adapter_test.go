package runtimecfg

import (
	"context"
	"errors"
	"reflect"
	"testing"
)

type telegramRunnerFunc func(
	context.Context,
	OrchestratorRequest,
) (OrchestratorResult, error)

func (runner telegramRunnerFunc) Run(
	ctx context.Context,
	request OrchestratorRequest,
) (OrchestratorResult, error) {
	return runner(ctx, request)
}

type fakeTelegramGateway struct {
	nextMessageID int64
	replies       []string
	edits         []string
	voices        [][]byte
	voiceNames    []string
	failEdit      bool
}

func (gateway *fakeTelegramGateway) ReplyText(
	_ context.Context,
	_ int64,
	_ int64,
	_ int64,
	text string,
) (int64, error) {
	gateway.nextMessageID++
	gateway.replies = append(gateway.replies, text)
	return gateway.nextMessageID, nil
}

func (gateway *fakeTelegramGateway) EditText(
	_ context.Context,
	_ int64,
	_ int64,
	text string,
) error {
	if gateway.failEdit {
		gateway.failEdit = false
		return errors.New("edit failed")
	}
	gateway.edits = append(gateway.edits, text)
	return nil
}

func (gateway *fakeTelegramGateway) ReplyVoice(
	_ context.Context,
	_ int64,
	_ int64,
	_ int64,
	audio []byte,
	filename string,
) error {
	gateway.voices = append(gateway.voices, append([]byte(nil), audio...))
	gateway.voiceNames = append(gateway.voiceNames, filename)
	return nil
}

func TestTelegramAtriRuntimeProgressAttachmentVoiceAndFinalChunks(t *testing.T) {
	gateway := &fakeTelegramGateway{}
	runner := telegramRunnerFunc(func(
		ctx context.Context,
		request OrchestratorRequest,
	) (OrchestratorResult, error) {
		if request.PublicText != "xin chào" {
			t.Fatalf("public text=%q", request.PublicText)
		}
		if request.ToolContext.UserID != 100 ||
			request.ToolContext.ChatID != -200 ||
			request.ToolContext.ThreadID != 7 {
			t.Fatalf("tool context=%+v", request.ToolContext)
		}
		attachment, ok := request.ToolContext.Metadata["attachment_bytes"].([]byte)
		if !ok || string(attachment) != "image-data" {
			t.Fatalf("attachment=%v", request.ToolContext.Metadata["attachment_bytes"])
		}
		if request.ToolContext.Metadata["attachment_mime_type"] != "image/png" {
			t.Fatalf("mime=%v", request.ToolContext.Metadata["attachment_mime_type"])
		}
		sender, ok := request.ToolContext.Metadata["voice_sender"].(GoogleVoiceSender)
		if !ok {
			t.Fatalf("voice sender=%T", request.ToolContext.Metadata["voice_sender"])
		}
		if err := sender(
			ctx,
			request.ToolContext,
			[]byte("voice-data"),
			"atri.ogg",
		); err != nil {
			t.Fatal(err)
		}
		if request.ProgressCallback == nil {
			t.Fatal("progress callback is nil")
		}
		if err := request.ProgressCallback(1, "Đang xử lý"); err != nil {
			t.Fatal(err)
		}
		if err := request.ProgressCallback(2, ""); err != nil {
			t.Fatal(err)
		}
		return OrchestratorResult{
			Text:   "Kết quả cuối\nphần hai",
			Chunks: []string{"Kết quả cuối", "phần hai"},
		}, nil
	})

	runtime := TelegramAtriRuntime{Runner: runner, Gateway: gateway}
	incoming := TelegramIncomingMessage{
		ChatID:             -200,
		MessageID:          300,
		ThreadID:           7,
		UserID:             100,
		Text:               " xin chào ",
		AttachmentBytes:    []byte("image-data"),
		AttachmentMIMEType: " image/png ",
	}
	result, err := runtime.Handle(
		context.Background(),
		incoming,
		OrchestratorRequest{Mode: "chat"},
	)
	if err != nil {
		t.Fatal(err)
	}
	if result.Text != "Kết quả cuối\nphần hai" {
		t.Fatalf("result=%+v", result)
	}
	if !reflect.DeepEqual(gateway.replies, []string{"Đang xử lý", "phần hai"}) {
		t.Fatalf("replies=%v", gateway.replies)
	}
	if !reflect.DeepEqual(gateway.edits, []string{"Kết quả cuối"}) {
		t.Fatalf("edits=%v", gateway.edits)
	}
	if len(gateway.voices) != 1 || string(gateway.voices[0]) != "voice-data" {
		t.Fatalf("voices=%q", gateway.voices)
	}
	if !reflect.DeepEqual(gateway.voiceNames, []string{"atri.ogg"}) {
		t.Fatalf("voice names=%v", gateway.voiceNames)
	}
}

func TestTelegramProgressFinalEditFallbackCreatesReply(t *testing.T) {
	gateway := &fakeTelegramGateway{failEdit: true}
	responder := &telegramProgressResponder{
		gateway:  gateway,
		chatID:   1,
		replyTo:  2,
		threadID: 3,
	}
	ctx := context.Background()
	if err := responder.Callback(ctx, 1, "preview"); err != nil {
		t.Fatal(err)
	}
	if err := responder.Finalize(ctx, []string{"final"}); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(gateway.replies, []string{"preview", "final"}) {
		t.Fatalf("replies=%v", gateway.replies)
	}
	if len(gateway.edits) != 0 {
		t.Fatalf("edits=%v", gateway.edits)
	}
}

func TestTelegramAtriRuntimeValidationAndRunnerFailure(t *testing.T) {
	if _, err := (&TelegramAtriRuntime{}).Handle(
		context.Background(),
		TelegramIncomingMessage{},
		OrchestratorRequest{},
	); err == nil {
		t.Fatal("missing runner should fail")
	}

	runtime := TelegramAtriRuntime{
		Runner: telegramRunnerFunc(func(
			context.Context,
			OrchestratorRequest,
		) (OrchestratorResult, error) {
			return OrchestratorResult{}, errors.New("runner failed")
		}),
		Gateway: &fakeTelegramGateway{},
	}
	if _, err := runtime.Handle(
		context.Background(),
		TelegramIncomingMessage{},
		OrchestratorRequest{},
	); err == nil || err.Error() != "runner failed" {
		t.Fatalf("err=%v", err)
	}
}
