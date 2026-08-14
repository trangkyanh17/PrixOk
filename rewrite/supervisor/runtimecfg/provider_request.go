package runtimecfg

import "strings"

func thinkingEffort(level string) string {
	switch strings.ToLower(strings.TrimSpace(level)) {
	case "minimal", "low":
		return "low"
	case "high":
		return "high"
	default:
		return "medium"
	}
}

func BuildProviderHeaders(provider, apiKey string) map[string]string {
	headers := map[string]string{
		"Authorization": "Bearer " + apiKey,
		"Content-Type":  "application/json",
	}
	if strings.EqualFold(strings.TrimSpace(provider), "openrouter") {
		headers["X-Title"] = "Atri AI"
	}
	return headers
}

func BuildChatPayload(
	provider string,
	model string,
	messages []map[string]string,
	thinkingLevel string,
	maxTokens int,
	temperature float64,
) map[string]any {
	provider = strings.ToLower(strings.TrimSpace(provider))
	level := strings.ToLower(strings.TrimSpace(thinkingLevel))
	if level == "" {
		level = "medium"
	}
	payload := map[string]any{
		"model":       model,
		"messages":    messages,
		"max_tokens":  maxTokens,
		"temperature": temperature,
	}

	switch provider {
	case "cerebras":
		if model == "zai-glm-4.7" && level == "minimal" {
			payload["reasoning_effort"] = "none"
		} else {
			payload["reasoning_effort"] = thinkingEffort(level)
		}
	case "groq":
		payload["reasoning_effort"] = thinkingEffort(level)
	case "openrouter":
		if model != "openrouter/free" && model != "openrouter/auto" {
			effort := level
			switch effort {
			case "minimal", "low", "medium", "high":
			default:
				effort = "medium"
			}
			payload["reasoning"] = map[string]any{
				"effort":  effort,
				"exclude": true,
			}
		}
	}

	if provider == "groq" && model == "qwen/qwen3.6-27b" {
		if level == "minimal" || level == "low" {
			payload["reasoning_effort"] = "none"
			delete(payload, "reasoning_format")
		} else {
			payload["reasoning_effort"] = "default"
			payload["reasoning_format"] = "hidden"
		}
	}

	return payload
}
