package runtimecfg

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

func overrideFreeProviderURL(t *testing.T, name, url string) {
	t.Helper()
	original := FreeProviderDefinitions[name]
	updated := original
	updated.URL = url
	FreeProviderDefinitions[name] = updated
	t.Cleanup(func() {
		FreeProviderDefinitions[name] = original
	})
}

func textCurrent(value string) []map[string]any {
	return []map[string]any{{"text": value}}
}

func TestGenerateFreeChatSkipsWhenDisabledOrNonText(t *testing.T) {
	runtime := FreePoolRuntime{Values: map[string]string{"ATRI_FREE_POOL_ENABLED": "off"}}
	reply, err := runtime.GenerateFreeChat(context.Background(), FreeChatRequest{
		CurrentParts: textCurrent("hello"),
	})
	if err != nil || reply != nil {
		t.Fatalf("disabled reply=%+v err=%v", reply, err)
	}

	runtime.Values = map[string]string{}
	reply, err = runtime.GenerateFreeChat(context.Background(), FreeChatRequest{
		CurrentParts: []map[string]any{{"inlineData": map[string]any{"x": 1}}},
	})
	if err != nil || reply != nil {
		t.Fatalf("non-text reply=%+v err=%v", reply, err)
	}
}

func TestGenerateFreeChatManualVertexSkipsFreePool(t *testing.T) {
	state := DefaultControlState()
	state.ProviderMode = "vertex"
	runtime := FreePoolRuntime{Control: state}
	reply, err := runtime.GenerateFreeChat(context.Background(), FreeChatRequest{
		CurrentParts: textCurrent("hello"),
	})
	if err != nil || reply != nil {
		t.Fatalf("reply=%+v err=%v", reply, err)
	}
}

func TestGenerateFreeChatSmartUsesTaskFixedModel(t *testing.T) {
	var model string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode: %v", err)
		}
		model, _ = payload["model"].(string)
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"hello from groq"}}]}`))
	}))
	defer server.Close()
	overrideFreeProviderURL(t, "groq_gptoss", server.URL)

	runtime := FreePoolRuntime{
		Values: map[string]string{
			"GROQ_API_KEY": "secret",
		},
		Control: DefaultControlState(),
		Router:  NewSmartRouterState(),
	}
	reply, err := runtime.GenerateFreeChat(context.Background(), FreeChatRequest{
		SystemInstruction: "system",
		CurrentParts:      textCurrent("hello"),
		ThinkingLevel:     "high",
		TaskType:          "chat",
	})
	if err != nil {
		t.Fatal(err)
	}
	if reply == nil || reply.Text != "hello from groq" || reply.Provider != "groq" {
		t.Fatalf("reply=%+v", reply)
	}
	if model != "qwen/qwen3.6-27b" || reply.Model != model {
		t.Fatalf("model=%q reply=%+v", model, reply)
	}
}

func TestGenerateFreeChat429UsesOpenRouterGlobalCooldown(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = w.Write([]byte(`{"error":"rate limited"}`))
	}))
	defer server.Close()
	overrideFreeProviderURL(t, "openrouter_free", server.URL)

	control := DefaultControlState()
	control.ProviderMode = "openrouter"
	router := NewSmartRouterState()
	runtime := FreePoolRuntime{
		Values:  map[string]string{"OPENROUTER_API_KEY": "secret"},
		Control: control,
		Router:  router,
	}
	reply, err := runtime.GenerateFreeChat(context.Background(), FreeChatRequest{
		CurrentParts: textCurrent("hello"),
	})
	if err != nil || reply != nil {
		t.Fatalf("reply=%+v err=%v", reply, err)
	}
	if router.CooldownUntil["openrouter_free_global"] <= 0 {
		t.Fatalf("cooldowns=%v", router.CooldownUntil)
	}
	if _, ok := router.CooldownUntil["openrouter_free"]; ok {
		t.Fatalf("model-local cooldown should not be used for 429: %v", router.CooldownUntil)
	}
}

func TestGenerateFreeChatTerminalErrorMarksModelDeadAndPersists(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"error":"model not found"}`))
	}))
	defer server.Close()
	overrideFreeProviderURL(t, "groq_gptoss", server.URL)

	control := DefaultControlState()
	control.ProviderMode = "groq"
	capabilities := BlankCapabilityState()
	statePath := filepath.Join(t.TempDir(), "capabilities.json")
	runtime := FreePoolRuntime{
		Values:              map[string]string{"GROQ_API_KEY": "secret"},
		Control:             control,
		Capabilities:        &capabilities,
		CapabilityStatePath: statePath,
		Router:              NewSmartRouterState(),
	}
	reply, err := runtime.GenerateFreeChat(context.Background(), FreeChatRequest{
		CurrentParts: textCurrent("hello"),
	})
	if err != nil || reply != nil {
		t.Fatalf("reply=%+v err=%v", reply, err)
	}
	if got := capabilities.CapabilityModelStatus("groq", "openai/gpt-oss-120b"); got != "dead" {
		t.Fatalf("status=%q", got)
	}
	loaded := LoadCapabilityState(statePath)
	if got := loaded.CapabilityModelStatus("groq", "openai/gpt-oss-120b"); got != "dead" {
		t.Fatalf("persisted status=%q", got)
	}
}

func TestCallFreeProviderCapturesRateHeadersAndResponseText(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("x-ratelimit-remaining-requests", "50")
		w.Header().Set("x-ratelimit-limit-requests", "100")
		w.Header().Set("x-ratelimit-reset-requests", "60s")
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":[{"text":"one"},{"text":"two"}]}}]}`))
	}))
	defer server.Close()

	router := NewSmartRouterState()
	text, err := CallFreeProvider(
		context.Background(),
		server.Client(),
		FreeProviderSpec{Provider: "groq", URL: server.URL, Model: "openai/gpt-oss-120b"},
		"secret",
		[]map[string]string{{"role": "user", "content": "hello"}},
		"medium",
		128,
		5,
		router,
	)
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(text) != "one\ntwo" {
		t.Fatalf("text=%q", text)
	}
	if got := router.RequestRatio["groq_gptoss"]; got != 0.5 {
		t.Fatalf("request ratio=%v", got)
	}
}
