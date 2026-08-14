package runtimecfg

type ModelChoice struct {
	Model string
	Label string
}

var CandidateModelChoices = map[string][]ModelChoice{
	"cerebras": {
		{Model: "gpt-oss-120b", Label: "OSS120B"},
		{Model: "zai-glm-4.7", Label: "GLM4.7-P"},
	},
	"groq": {
		{Model: "qwen/qwen3.6-27b", Label: "QWEN3.6"},
		{Model: "openai/gpt-oss-120b", Label: "OSS120B"},
		{Model: "openai/gpt-oss-20b", Label: "OSS20B"},
	},
	"openrouter": {
		{Model: "openrouter/free", Label: "FREE"},
		{Model: "cohere/north-mini-code:free", Label: "NORTH"},
		{Model: "nvidia/nemotron-3-super-120b-a12b:free", Label: "NEMO3S"},
		{Model: "google/gemma-4-26b-a4b-it:free", Label: "GEMMA4"},
		{Model: "openai/gpt-oss-20b:free", Label: "OSS20B"},
		{Model: "nvidia/nemotron-3-ultra-550b-a55b:free", Label: "NEMO3U"},
	},
	"vertex": {
		{Model: "auto", Label: "AUTO"},
		{Model: "gemini-3-flash-preview", Label: "3FLASH"},
		{Model: "gemini-3.1-flash-lite", Label: "3.1LITE"},
	},
}
