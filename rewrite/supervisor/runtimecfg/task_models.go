package runtimecfg

import "strings"

type ModelMetadata struct {
	Tier         string
	Stability    string
	Privacy      string
	Context      int
	MaxOutput    int
	Capabilities []string
	Adapter      string
}

var ModelMetadataRegistry = map[ProviderModel]ModelMetadata{
	{Provider: "cerebras", Model: "gpt-oss-120b"}: {
		Tier: "free", Stability: "production", Privacy: "public_only", Context: 131072,
		Capabilities: []string{"chat", "reasoning", "coding"},
	},
	{Provider: "cerebras", Model: "zai-glm-4.7"}: {
		Tier: "free", Stability: "preview", Privacy: "public_only",
		Capabilities: []string{"chat", "reasoning"},
	},
	{Provider: "groq", Model: "qwen/qwen3.6-27b"}: {
		Tier: "account", Stability: "preview", Privacy: "public_only", Context: 131072, MaxOutput: 16384,
		Capabilities: []string{"chat", "reasoning", "tools", "json", "vision", "coding", "agent"},
		Adapter:      "groq_qwen36",
	},
	{Provider: "groq", Model: "openai/gpt-oss-120b"}: {
		Tier: "free", Stability: "production", Privacy: "public_only",
		Capabilities: []string{"chat", "reasoning", "coding"},
	},
	{Provider: "groq", Model: "openai/gpt-oss-20b"}: {
		Tier: "free", Stability: "production", Privacy: "public_only",
		Capabilities: []string{"chat", "reasoning", "coding"},
	},
	{Provider: "openrouter", Model: "openrouter/free"}: {
		Tier: "free", Stability: "dynamic", Privacy: "public_only",
		Capabilities: []string{"chat"}, Adapter: "dynamic_auto_only",
	},
	{Provider: "openrouter", Model: "cohere/north-mini-code:free"}: {
		Tier: "free", Stability: "free_endpoint", Privacy: "public_only", Context: 262144, MaxOutput: 65536,
		Capabilities: []string{"chat", "reasoning", "tools", "json", "coding", "agent"},
	},
	{Provider: "openrouter", Model: "nvidia/nemotron-3-super-120b-a12b:free"}: {
		Tier: "free", Stability: "free_endpoint", Privacy: "public_only_strict", Context: 262144,
		Capabilities: []string{"chat", "reasoning", "tools", "research", "long_context", "agent"},
	},
	{Provider: "openrouter", Model: "google/gemma-4-26b-a4b-it:free"}: {
		Tier: "free", Stability: "free_endpoint", Privacy: "public_only", Context: 262144, MaxOutput: 32768,
		Capabilities: []string{"chat", "reasoning", "tools", "json", "vision"},
	},
	{Provider: "openrouter", Model: "openai/gpt-oss-20b:free"}: {
		Tier: "free", Stability: "free_endpoint", Privacy: "public_only",
		Capabilities: []string{"chat", "reasoning", "coding"},
	},
	{Provider: "openrouter", Model: "nvidia/nemotron-3-ultra-550b-a55b:free"}: {
		Tier: "free", Stability: "free_endpoint", Privacy: "public_only",
		Capabilities: []string{"chat", "reasoning", "research", "agent"},
	},
}

var TaskModelOrder = map[string][]ProviderModel{
	"chat": {
		{Provider: "groq", Model: "qwen/qwen3.6-27b"},
		{Provider: "cerebras", Model: "gpt-oss-120b"},
		{Provider: "openrouter", Model: "google/gemma-4-26b-a4b-it:free"},
		{Provider: "openrouter", Model: "openrouter/free"},
	},
	"coding": {
		{Provider: "groq", Model: "qwen/qwen3.6-27b"},
		{Provider: "cerebras", Model: "gpt-oss-120b"},
		{Provider: "openrouter", Model: "cohere/north-mini-code:free"},
	},
	"coding_agentic": {
		{Provider: "openrouter", Model: "cohere/north-mini-code:free"},
		{Provider: "groq", Model: "qwen/qwen3.6-27b"},
		{Provider: "cerebras", Model: "gpt-oss-120b"},
	},
	"tools": {
		{Provider: "vertex", Model: "auto"},
	},
	"research": {
		{Provider: "groq", Model: "qwen/qwen3.6-27b"},
		{Provider: "openrouter", Model: "google/gemma-4-26b-a4b-it:free"},
		{Provider: "openrouter", Model: "nvidia/nemotron-3-super-120b-a12b:free"},
	},
	"research_long": {
		{Provider: "openrouter", Model: "nvidia/nemotron-3-super-120b-a12b:free"},
		{Provider: "openrouter", Model: "google/gemma-4-26b-a4b-it:free"},
		{Provider: "groq", Model: "qwen/qwen3.6-27b"},
	},
}

func ModelMetadataFor(provider, model string) (ModelMetadata, bool) {
	key := ProviderModel{Provider: strings.ToLower(strings.TrimSpace(provider)), Model: strings.TrimSpace(model)}
	metadata, ok := ModelMetadataRegistry[key]
	if !ok {
		return ModelMetadata{}, false
	}
	metadata.Capabilities = append([]string(nil), metadata.Capabilities...)
	return metadata, true
}

func TaskModelCandidates(task string, requirePublicSafe bool, status map[ProviderModel]string) []ProviderModel {
	task = strings.ToLower(strings.TrimSpace(task))
	requested, ok := TaskModelOrder[task]
	if !ok {
		requested = TaskModelOrder["chat"]
	}
	visible := make([]ProviderModel, 0, len(requested))
	for _, item := range requested {
		if ModelStatus(item.Provider, item.Model, status) == "dead" {
			continue
		}
		metadata, exists := ModelMetadataFor(item.Provider, item.Model)
		if requirePublicSafe && exists && !strings.HasPrefix(metadata.Privacy, "public_only") {
			continue
		}
		visible = append(visible, item)
	}
	return visible
}
