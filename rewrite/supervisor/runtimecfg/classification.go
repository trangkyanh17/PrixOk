package runtimecfg

import (
	"fmt"
	"strings"
)

func IsTerminalModelError(statusCode int, hasStatus bool, errorText string) bool {
	if hasStatus && (statusCode == 404 || statusCode == 410) {
		return true
	}
	if !hasStatus || statusCode != 400 {
		return false
	}
	text := strings.ToLower(errorText)
	if !strings.Contains(text, "model") {
		return false
	}
	for _, hint := range []string{
		"not found",
		"does not exist",
		"doesn't exist",
		"unknown model",
		"invalid model",
		"decommissioned",
		"no longer available",
	} {
		if strings.Contains(text, hint) {
			return true
		}
	}
	return false
}

func ClassifyProbe(statusCode int, hasStatus bool, text string) (string, string) {
	if hasStatus && statusCode >= 200 && statusCode < 300 {
		return "ok", "live_probe"
	}
	if IsTerminalModelError(statusCode, hasStatus, text) {
		return "dead", "model_not_available"
	}
	if !hasStatus {
		return "unknown", "network_error"
	}
	switch statusCode {
	case 429:
		return "unknown", "rate_limited"
	case 401:
		return "unknown", "key_invalid"
	case 403:
		return "unknown", "auth_or_plan"
	default:
		if statusCode >= 500 {
			return "unknown", "provider_error"
		}
		return "unknown", fmt.Sprintf("http_%d", statusCode)
	}
}

func ClassifyKeyCheck(statusCode int, hasStatus bool) (string, string) {
	if hasStatus && statusCode >= 200 && statusCode < 300 {
		return "ok", "key_valid"
	}
	if !hasStatus {
		return "unknown", "network_error"
	}
	switch statusCode {
	case 401:
		return "invalid", "key_invalid"
	case 403:
		return "denied", "auth_or_plan"
	case 429:
		return "unknown", "rate_limited"
	default:
		if statusCode >= 500 {
			return "unknown", "provider_error"
		}
		return "unknown", fmt.Sprintf("http_%d", statusCode)
	}
}
