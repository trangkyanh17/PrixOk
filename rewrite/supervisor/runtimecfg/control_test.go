package runtimecfg

import "testing"

func TestControlStateDefaultsAndNormalization(t *testing.T) {
	state := NormalizeControlState(ControlState{
		ProviderMode: "groq",
		Providers: map[string]ProviderSelection{
			"groq": {Model: "qwen/qwen3.6-27b", Thinking: "high"},
		},
	})
	if state.ProviderMode != "groq" {
		t.Fatalf("mode=%q", state.ProviderMode)
	}
	if got := state.Providers["groq"].Model; got != "qwen/qwen3.6-27b" {
		t.Fatalf("model=%q", got)
	}
	if got := state.Providers["openrouter"].Model; got != "openrouter/free" {
		t.Fatalf("default model=%q", got)
	}
}

func TestVertexAutoUsesFallback(t *testing.T) {
	state := DefaultControlState()
	if got := ResolveProviderModel(state, "vertex", "gemini-default"); got != "gemini-default" {
		t.Fatalf("model=%q", got)
	}
}

func TestAutoThinkingUsesFallback(t *testing.T) {
	state := DefaultControlState()
	if got := ResolveProviderThinking(state, "groq", "medium"); got != "medium" {
		t.Fatalf("thinking=%q", got)
	}
}
