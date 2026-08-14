package runtimecfg

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestVertexTextRuntimeCompletesSimpleResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"hello"}]}}]}`))
	}))
	defer server.Close()

	runtime := VertexTextRuntime{
		Client: server.Client(),
		URL:    server.URL,
		TokenProvider: func(context.Context, bool) (string, error) {
			return "token", nil
		},
		Sleep: func(context.Context, time.Duration) error { return nil },
	}
	text, err := runtime.Generate(context.Background(), map[string]any{
		"contents": []any{map[string]any{"role": "user", "parts": []any{map[string]any{"text": "hi"}}}},
	})
	if err != nil || text != "hello" {
		t.Fatalf("text=%q err=%v", text, err)
	}
}

func TestVertexTextRuntimeAutoContinuesTruncatedResponse(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode payload: %v", err)
		}
		contents, _ := payload["contents"].([]any)
		if calls == 1 {
			if len(contents) != 1 {
				t.Errorf("first contents=%v", contents)
			}
			_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"MAX_TOKENS","content":{"role":"model","parts":[{"text":"prefix ABCDEFGHIJKLMNOPQRSTUVWXYZ"}]}}]}`))
			return
		}
		if len(contents) != 3 {
			t.Errorf("second contents len=%d contents=%v", len(contents), contents)
		}
		continuation := contents[2].(map[string]any)
		parts := continuation["parts"].([]any)
		if parts[0].(map[string]any)["text"] != VertexContinuationPrompt {
			t.Errorf("continuation=%v", continuation)
		}
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"abcdefghijklmnopqrstuvwxyz suffix"}]}}]}`))
	}))
	defer server.Close()

	runtime := VertexTextRuntime{
		Client:                server.Client(),
		URL:                   server.URL,
		MaxContinuationRounds: 4,
		TokenProvider: func(context.Context, bool) (string, error) {
			return "token", nil
		},
		Sleep: func(context.Context, time.Duration) error { return nil },
	}
	text, err := runtime.Generate(context.Background(), map[string]any{
		"contents": []any{map[string]any{"role": "user", "parts": []any{map[string]any{"text": "hi"}}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if text != "prefix ABCDEFGHIJKLMNOPQRSTUVWXYZ suffix" || calls != 2 {
		t.Fatalf("text=%q calls=%d", text, calls)
	}
}

func TestVertexTextRuntimeRetriesEmptyTextWithExpectedDelay(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls <= 2 {
			_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"thoughtSignature":"hidden"}]}}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"done"}]}}]}`))
	}))
	defer server.Close()

	sleeps := []time.Duration{}
	runtime := VertexTextRuntime{
		Client:              server.Client(),
		URL:                 server.URL,
		MaxEmptyTextRetries: 2,
		TokenProvider: func(context.Context, bool) (string, error) {
			return "token", nil
		},
		Sleep: func(ctx context.Context, duration time.Duration) error {
			sleeps = append(sleeps, duration)
			return nil
		},
	}
	text, err := runtime.Generate(context.Background(), map[string]any{"contents": []any{}})
	if err != nil || text != "done" {
		t.Fatalf("text=%q err=%v", text, err)
	}
	if len(sleeps) != 2 || sleeps[0] != 350*time.Millisecond || sleeps[1] != 700*time.Millisecond {
		t.Fatalf("sleeps=%v", sleeps)
	}
}

func TestVertexTextRuntimeRejectsFunctionCalls(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"functionCall":{"name":"tool_x","args":{}}}]}}]}`))
	}))
	defer server.Close()

	runtime := VertexTextRuntime{
		Client: server.Client(),
		URL:    server.URL,
		TokenProvider: func(context.Context, bool) (string, error) {
			return "token", nil
		},
		Sleep: func(context.Context, time.Duration) error { return nil },
	}
	_, err := runtime.Generate(context.Background(), map[string]any{"contents": []any{}})
	if err == nil || !strings.Contains(err.Error(), "function calls") {
		t.Fatalf("err=%v", err)
	}
}

func TestVertexTextRuntimeDoesNotMutateCallerPayload(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if calls == 1 {
			_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"MAX_TOKENS","content":{"role":"model","parts":[{"text":"one"}]}}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"two"}]}}]}`))
	}))
	defer server.Close()

	payload := map[string]any{
		"contents": []any{map[string]any{"role": "user", "parts": []any{map[string]any{"text": "hi"}}}},
	}
	runtime := VertexTextRuntime{
		Client: server.Client(),
		URL:    server.URL,
		TokenProvider: func(context.Context, bool) (string, error) {
			return "token", nil
		},
		Sleep: func(context.Context, time.Duration) error { return nil },
	}
	_, err := runtime.Generate(context.Background(), payload)
	if err != nil {
		t.Fatal(err)
	}
	if contents := payload["contents"].([]any); len(contents) != 1 {
		t.Fatalf("caller payload mutated: %v", payload)
	}
}
