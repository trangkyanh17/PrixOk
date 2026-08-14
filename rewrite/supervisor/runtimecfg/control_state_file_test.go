package runtimecfg

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadControlStateCreatesDefaultFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "atri", "control.json")
	state, err := LoadControlState(path)
	if err != nil {
		t.Fatal(err)
	}
	if state.ProviderMode != "smart" || state.Providers["groq"].Model != "openai/gpt-oss-120b" {
		t.Fatalf("state=%+v", state)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != 0o600 {
		t.Fatalf("mode=%o want=600", got)
	}
}

func TestControlStateRoundTripNormalizesValues(t *testing.T) {
	path := filepath.Join(t.TempDir(), "control.json")
	input := ControlState{
		ProviderMode: "GROQ",
		Providers: map[string]ProviderSelection{
			"groq": {Model: "qwen/qwen3.6-27b", Thinking: "HIGH"},
		},
	}
	if err := SaveControlState(path, input); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadControlState(path)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.ProviderMode != "groq" {
		t.Fatalf("mode=%q", loaded.ProviderMode)
	}
	if got := loaded.Providers["groq"]; got.Model != "qwen/qwen3.6-27b" || got.Thinking != "high" {
		t.Fatalf("groq=%+v", got)
	}
	if got := loaded.Providers["openrouter"].Model; got != "openrouter/free" {
		t.Fatalf("default openrouter=%q", got)
	}
}

func TestLoadControlStateMalformedJSONFallsBack(t *testing.T) {
	path := filepath.Join(t.TempDir(), "control.json")
	if err := os.WriteFile(path, []byte("{not-json"), 0o600); err != nil {
		t.Fatal(err)
	}
	state, err := LoadControlState(path)
	if err != nil {
		t.Fatal(err)
	}
	if state.ProviderMode != "smart" || state.Providers["vertex"].Model != "auto" {
		t.Fatalf("state=%+v", state)
	}
}

func TestHealControlStateMovesDeadSelectionToLiveModel(t *testing.T) {
	state := DefaultControlState()
	state.ProviderMode = "groq"
	state.Providers["groq"] = ProviderSelection{Model: "openai/gpt-oss-120b", Thinking: "minimal"}

	capabilities := BlankCapabilityState()
	capabilities.MarkModelUnavailable("groq", "openai/gpt-oss-120b", "gone", 100)
	capabilities.MarkModelAvailable("groq", "qwen/qwen3.6-27b", "live", 100)

	healed, changed := HealControlState(state, capabilities)
	if !changed {
		t.Fatal("expected state to change")
	}
	groq := healed.Providers["groq"]
	if groq.Model != "qwen/qwen3.6-27b" {
		t.Fatalf("model=%q", groq.Model)
	}
	if groq.Thinking != "minimal" {
		t.Fatalf("thinking=%q", groq.Thinking)
	}
	if healed.ProviderMode != "groq" {
		t.Fatalf("mode=%q", healed.ProviderMode)
	}
}

func TestHealControlStateFallsBackToSmartWhenProviderAllDead(t *testing.T) {
	state := DefaultControlState()
	state.ProviderMode = "cerebras"
	capabilities := BlankCapabilityState()
	for _, choice := range CandidateModelChoices["cerebras"] {
		capabilities.MarkModelUnavailable("cerebras", choice.Model, "gone", 100)
	}

	healed, changed := HealControlState(state, capabilities)
	if !changed {
		t.Fatal("expected state to change")
	}
	if healed.ProviderMode != "smart" {
		t.Fatalf("mode=%q", healed.ProviderMode)
	}
}

func TestHealAndSaveControlStatePersistsCorrection(t *testing.T) {
	path := filepath.Join(t.TempDir(), "control.json")
	state := DefaultControlState()
	state.Providers["openrouter"] = ProviderSelection{
		Model:    "openrouter/free",
		Thinking: "high",
	}
	capabilities := BlankCapabilityState()

	healed, changed, err := HealAndSaveControlState(path, state, capabilities)
	if err != nil {
		t.Fatal(err)
	}
	if !changed || healed.Providers["openrouter"].Thinking != "auto" {
		t.Fatalf("changed=%v healed=%+v", changed, healed.Providers["openrouter"])
	}
	loaded, err := LoadControlState(path)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Providers["openrouter"].Thinking != "auto" {
		t.Fatalf("loaded=%+v", loaded.Providers["openrouter"])
	}
}
