package runtimecfg

import (
	"strconv"
	"strings"
)

func boundedInt(values map[string]string, key string, fallback, minimum, maximum int) int {
	value, err := strconv.Atoi(strings.TrimSpace(values[key]))
	if err != nil {
		value = fallback
	}
	if value < minimum {
		return minimum
	}
	if value > maximum {
		return maximum
	}
	return value
}

func FreePoolDynamicMaxTokens(values map[string]string, thinkingLevel string) int {
	globalCap := boundedInt(values, "ATRI_FREE_MAX_TOKENS", 4096, 64, 16384)
	level := strings.ToLower(strings.TrimSpace(thinkingLevel))
	defaults := map[string]int{
		"minimal": 512,
		"low":     1024,
		"medium":  2048,
		"high":    3072,
	}
	keys := map[string]string{
		"minimal": "ATRI_FREE_MAX_TOKENS_MINIMAL",
		"low":     "ATRI_FREE_MAX_TOKENS_LOW",
		"medium":  "ATRI_FREE_MAX_TOKENS_MEDIUM",
		"high":    "ATRI_FREE_MAX_TOKENS_HIGH",
	}
	if _, ok := defaults[level]; !ok {
		level = "medium"
	}
	levelCap := boundedInt(values, keys[level], defaults[level], 64, 8192)
	if levelCap < globalCap {
		return levelCap
	}
	return globalCap
}

func FreePoolMaxAttempts(values map[string]string, providerMode, taskType string) int {
	attempts := boundedInt(values, "ATRI_FREE_MAX_ATTEMPTS", 3, 1, 5)
	if strings.EqualFold(strings.TrimSpace(providerMode), "smart") && NormalizeFreePoolTask(taskType) == "chat" && attempts < 4 {
		return 4
	}
	return attempts
}

func FreePoolRequestTimeoutSeconds(values map[string]string) int {
	return boundedInt(values, "ATRI_FREE_REQUEST_TIMEOUT", 20, 5, 60)
}

func FreePoolFailureCooldownSeconds(statusCode int, hasStatus bool) float64 {
	if !hasStatus {
		return 10
	}
	switch statusCode {
	case 401, 403:
		return 300
	case 429:
		return 60
	default:
		if statusCode >= 500 {
			return 20
		}
		return 10
	}
}

func FreePoolUnexpectedFailureCooldownSeconds() float64 {
	return 15
}
