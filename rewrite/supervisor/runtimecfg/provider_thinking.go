package runtimecfg

import "strings"

var defaultProviderThinking = []string{"auto", "minimal", "low", "medium", "high"}

var providerThinkingByModel = map[ProviderModel][]string{
	{Provider: "cerebras", Model: "gpt-oss-120b"}:               {"auto", "low", "medium", "high"},
	{Provider: "cerebras", Model: "zai-glm-4.7"}:                {"auto", "minimal", "low", "medium", "high"},
	{Provider: "groq", Model: "qwen/qwen3.6-27b"}:               {"auto", "minimal", "low", "medium", "high"},
	{Provider: "groq", Model: "openai/gpt-oss-120b"}:            {"auto", "low", "medium", "high"},
	{Provider: "groq", Model: "openai/gpt-oss-20b"}:             {"auto", "low", "medium", "high"},
	{Provider: "openrouter", Model: "openrouter/free"}:           {"auto"},
	{Provider: "openrouter", Model: "cohere/north-mini-code:free"}: {"auto", "minimal", "low", "medium", "high"},
	{Provider: "openrouter", Model: "nvidia/nemotron-3-super-120b-a12b:free"}: {"auto", "minimal", "low", "medium", "high"},
	{Provider: "openrouter", Model: "google/gemma-4-26b-a4b-it:free"}: {"auto", "minimal", "low", "medium", "high"},
	{Provider: "openrouter", Model: "openai/gpt-oss-20b:free"}: {"auto", "minimal", "low", "medium", "high"},
	{Provider: "openrouter", Model: "nvidia/nemotron-3-ultra-550b-a55b:free"}: {"auto", "minimal", "low", "medium", "high"},
	{Provider: "vertex", Model: "auto"}:                       {"auto", "minimal", "low", "medium", "high"},
	{Provider: "vertex", Model: "gemini-3-flash-preview"}:    {"auto", "minimal", "low", "medium", "high"},
	{Provider: "vertex", Model: "gemini-3.1-flash-lite"}:     {"auto", "minimal", "low", "medium", "high"},
}

func SupportedProviderThinkingLevels(provider, model string) []string {
	key := ProviderModel{
		Provider: strings.ToLower(strings.TrimSpace(provider)),
		Model:    strings.TrimSpace(model),
	}
	levels, ok := providerThinkingByModel[key]
	if !ok {
		levels = defaultProviderThinking
	}
	return append([]string(nil), levels...)
}

func HealProviderThinking(provider, model, value string) string {
	levels := SupportedProviderThinkingLevels(provider, model)
	value = strings.ToLower(strings.TrimSpace(value))
	for _, level := range levels {
		if value == level {
			return value
		}
	}
	for _, level := range levels {
		if level == "auto" {
			return "auto"
		}
	}
	return levels[0]
}
