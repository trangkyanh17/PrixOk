package runtimecfg

import "strings"

type FreeProviderSpec struct {
	Provider string
	KeyName  string
	URL      string
	Model    string
}

var FreeProviderDefinitions = map[string]FreeProviderSpec{
	"cerebras_gptoss": {
		Provider: "cerebras", KeyName: "CEREBRAS_API_KEY",
		URL: "https://api.cerebras.ai/v1/chat/completions", Model: "gpt-oss-120b",
	},
	"groq_gptoss": {
		Provider: "groq", KeyName: "GROQ_API_KEY",
		URL: "https://api.groq.com/openai/v1/chat/completions", Model: "openai/gpt-oss-120b",
	},
	"openrouter_free": {
		Provider: "openrouter", KeyName: "OPENROUTER_API_KEY",
		URL: "https://openrouter.ai/api/v1/chat/completions", Model: "openrouter/free",
	},
	"openrouter_gemma4": {
		Provider: "openrouter", KeyName: "OPENROUTER_API_KEY",
		URL: "https://openrouter.ai/api/v1/chat/completions", Model: "google/gemma-4-26b-a4b-it:free",
	},
	"openrouter_north": {
		Provider: "openrouter", KeyName: "OPENROUTER_API_KEY",
		URL: "https://openrouter.ai/api/v1/chat/completions", Model: "cohere/north-mini-code:free",
	},
	"openrouter_nemotron_super": {
		Provider: "openrouter", KeyName: "OPENROUTER_API_KEY",
		URL: "https://openrouter.ai/api/v1/chat/completions", Model: "nvidia/nemotron-3-super-120b-a12b:free",
	},
}

var FreePoolTaskChains = map[string][]string{
	"chat":           {"groq_gptoss", "cerebras_gptoss", "openrouter_gemma4", "openrouter_free"},
	"coding":         {"groq_gptoss", "cerebras_gptoss", "openrouter_north"},
	"coding_agentic": {"openrouter_north", "groq_gptoss", "cerebras_gptoss"},
	"research":       {"groq_gptoss", "openrouter_gemma4", "openrouter_nemotron_super"},
	"research_long":  {"openrouter_nemotron_super", "openrouter_gemma4", "groq_gptoss"},
}

var FreePoolTaskFixedModels = map[string]map[string]string{
	"chat": {
		"groq_gptoss": "qwen/qwen3.6-27b", "cerebras_gptoss": "gpt-oss-120b",
		"openrouter_gemma4": "google/gemma-4-26b-a4b-it:free", "openrouter_free": "openrouter/free",
	},
	"coding": {
		"groq_gptoss": "qwen/qwen3.6-27b", "cerebras_gptoss": "gpt-oss-120b",
		"openrouter_north": "cohere/north-mini-code:free",
	},
	"coding_agentic": {
		"openrouter_north": "cohere/north-mini-code:free", "groq_gptoss": "qwen/qwen3.6-27b",
		"cerebras_gptoss": "gpt-oss-120b",
	},
	"research": {
		"groq_gptoss": "qwen/qwen3.6-27b", "openrouter_gemma4": "google/gemma-4-26b-a4b-it:free",
		"openrouter_nemotron_super": "nvidia/nemotron-3-super-120b-a12b:free",
	},
	"research_long": {
		"openrouter_nemotron_super": "nvidia/nemotron-3-super-120b-a12b:free",
		"openrouter_gemma4":         "google/gemma-4-26b-a4b-it:free", "groq_gptoss": "qwen/qwen3.6-27b",
	},
}

func NormalizeFreePoolTask(task string) string {
	task = strings.ToLower(strings.TrimSpace(task))
	if _, ok := FreePoolTaskChains[task]; !ok {
		return "chat"
	}
	return task
}

func FreePoolTaskChain(task string) []string {
	task = NormalizeFreePoolTask(task)
	return append([]string(nil), FreePoolTaskChains[task]...)
}

func FreePoolTaskFixedModel(task, name string) string {
	task = NormalizeFreePoolTask(task)
	return FreePoolTaskFixedModels[task][name]
}

func FreePoolGlobalCooldownKey(spec FreeProviderSpec) string {
	if strings.EqualFold(spec.Provider, "openrouter") {
		return "openrouter_free_global"
	}
	return ""
}

func FreePoolFailureCooldownKey(name string, spec FreeProviderSpec, statusCode int) string {
	if statusCode == 429 {
		if global := FreePoolGlobalCooldownKey(spec); global != "" {
			return global
		}
	}
	return name
}

func FreePoolCooldownUntil(name string, spec FreeProviderSpec, cooldown map[string]float64) float64 {
	local := cooldown[name]
	globalKey := FreePoolGlobalCooldownKey(spec)
	if globalKey == "" || cooldown[globalKey] <= local {
		return local
	}
	return cooldown[globalKey]
}
