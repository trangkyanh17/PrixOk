package runtimecfg

import "strings"

type ProviderSelection struct {
	Model    string `json:"model"`
	Thinking string `json:"thinking"`
}

type ControlState struct {
	ProviderMode string                       `json:"provider_mode"`
	Providers    map[string]ProviderSelection `json:"providers"`
}

var providerOrder = []string{"cerebras", "groq", "openrouter", "vertex"}

var allowedProviderModes = map[string]bool{
	"smart": true, "cerebras": true, "groq": true, "openrouter": true, "vertex": true,
}

var allowedProviderModels = map[string]map[string]bool{
	"cerebras": {
		"gpt-oss-120b": true,
		"zai-glm-4.7":  true,
	},
	"groq": {
		"qwen/qwen3.6-27b":    true,
		"openai/gpt-oss-120b": true,
		"openai/gpt-oss-20b":  true,
	},
	"openrouter": {
		"openrouter/free":                        true,
		"cohere/north-mini-code:free":            true,
		"nvidia/nemotron-3-super-120b-a12b:free": true,
		"google/gemma-4-26b-a4b-it:free":         true,
		"openai/gpt-oss-20b:free":                true,
		"nvidia/nemotron-3-ultra-550b-a55b:free": true,
	},
	"vertex": {
		"auto":                   true,
		"gemini-3-flash-preview": true,
		"gemini-3.1-flash-lite":  true,
	},
}

func DefaultControlState() ControlState {
	return ControlState{
		ProviderMode: "smart",
		Providers: map[string]ProviderSelection{
			"cerebras":   {Model: "gpt-oss-120b", Thinking: "auto"},
			"groq":       {Model: "openai/gpt-oss-120b", Thinking: "auto"},
			"openrouter": {Model: "openrouter/free", Thinking: "auto"},
			"vertex":     {Model: "auto", Thinking: "auto"},
		},
	}
}

func NormalizeControlState(input ControlState) ControlState {
	state := DefaultControlState()
	mode := strings.ToLower(strings.TrimSpace(input.ProviderMode))
	if allowedProviderModes[mode] {
		state.ProviderMode = mode
	}

	for _, provider := range providerOrder {
		item, ok := input.Providers[provider]
		if !ok {
			continue
		}
		current := state.Providers[provider]
		model := strings.TrimSpace(item.Model)
		if allowedProviderModels[provider][model] {
			current.Model = model
		}
		thinking := strings.ToLower(strings.TrimSpace(item.Thinking))
		if thinking == "" {
			thinking = "auto"
		}
		current.Thinking = thinking
		state.Providers[provider] = current
	}
	return state
}

func ResolveProviderMode(state ControlState) string {
	mode := strings.ToLower(strings.TrimSpace(state.ProviderMode))
	if allowedProviderModes[mode] {
		return mode
	}
	return "smart"
}

func ResolveProviderModel(state ControlState, provider, fallback string) string {
	provider = strings.ToLower(strings.TrimSpace(provider))
	item, ok := state.Providers[provider]
	if !ok || !allowedProviderModels[provider][item.Model] {
		return fallback
	}
	if provider == "vertex" && item.Model == "auto" {
		return fallback
	}
	return item.Model
}

func ResolveProviderThinking(state ControlState, provider, fallback string) string {
	provider = strings.ToLower(strings.TrimSpace(provider))
	item, ok := state.Providers[provider]
	if !ok {
		return strings.ToLower(strings.TrimSpace(fallback))
	}
	value := HealProviderThinking(provider, item.Model, item.Thinking)
	if value == "auto" {
		return strings.ToLower(strings.TrimSpace(fallback))
	}
	return value
}
