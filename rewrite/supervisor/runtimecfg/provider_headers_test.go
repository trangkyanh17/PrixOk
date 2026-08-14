package runtimecfg

import "testing"

func TestBuildProviderHeaders(t *testing.T) {
	regular := BuildProviderHeaders("groq", "example-key")
	if regular["Authorization"] != "Bearer example-key" {
		t.Fatalf("authorization=%q", regular["Authorization"])
	}
	if regular["Content-Type"] != "application/json" {
		t.Fatalf("content-type=%q", regular["Content-Type"])
	}
	if _, ok := regular["X-Title"]; ok {
		t.Fatalf("unexpected X-Title: %v", regular)
	}

	openrouter := BuildProviderHeaders(" OpenRouter ", "example-key")
	if openrouter["X-Title"] != "Atri AI" {
		t.Fatalf("openrouter headers=%v", openrouter)
	}
}

func TestEmptyThinkingUsesMediumReasoning(t *testing.T) {
	payload := BuildChatPayload("groq", "openai/gpt-oss-120b", nil, "", 2048, 0.2)
	if payload["reasoning_effort"] != "medium" {
		t.Fatalf("payload=%v", payload)
	}
}
