package runtimecfg

import "testing"

func TestCerebrasMinimalOverride(t *testing.T) {
	payload := BuildChatPayload("cerebras", "zai-glm-4.7", nil, "minimal", 4096, 0.2)
	if payload["reasoning_effort"] != "none" {
		t.Fatalf("payload=%v", payload)
	}
}

func TestOpenRouterAutoSkipsReasoning(t *testing.T) {
	payload := BuildChatPayload("openrouter", "openrouter/auto", nil, "high", 4096, 0.2)
	if _, ok := payload["reasoning"]; ok {
		t.Fatalf("payload=%v", payload)
	}
}

func TestGroqQwenOverrides(t *testing.T) {
	low := BuildChatPayload("groq", "qwen/qwen3.6-27b", nil, "low", 4096, 0.2)
	if low["reasoning_effort"] != "none" {
		t.Fatalf("low=%v", low)
	}
	high := BuildChatPayload("groq", "qwen/qwen3.6-27b", nil, "high", 4096, 0.2)
	if high["reasoning_effort"] != "default" || high["reasoning_format"] != "hidden" {
		t.Fatalf("high=%v", high)
	}
}
