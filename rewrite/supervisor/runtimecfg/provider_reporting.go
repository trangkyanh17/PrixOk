package runtimecfg

import (
	"fmt"
	"strings"
)

var providerDisplayLabels = map[string]string{
	"cerebras":   "Cerebras",
	"groq":       "Groq",
	"openrouter": "OpenRouter",
	"vertex":     "Vertex",
}

func truncateRunes(value string, limit int) string {
	if limit <= 0 {
		return ""
	}
	runes := []rune(value)
	if len(runes) <= limit {
		return value
	}
	return string(runes[:limit])
}

func CompactAuditReport(report map[string]ProviderAuditReport) string {
	chunks := []string{}
	for _, provider := range capabilityProviderOrder {
		providerReport, ok := report[provider]
		if !ok {
			continue
		}
		counts := map[string]int{"ok": 0, "dead": 0, "unknown": 0}
		for _, result := range providerReport.Models {
			status := strings.ToLower(strings.TrimSpace(result.Status))
			if _, ok := counts[status]; !ok {
				status = "unknown"
			}
			counts[status]++
		}
		keyStatus := strings.TrimSpace(providerReport.Key.Status)
		if keyStatus == "" {
			keyStatus = "unknown"
		}
		chunks = append(chunks, fmt.Sprintf(
			"%s:key=%s,ok=%d,dead=%d,unknown=%d",
			provider,
			keyStatus,
			counts["ok"],
			counts["dead"],
			counts["unknown"],
		))
	}
	return strings.Join(chunks, " | ")
}

func AuditReportText(report map[string]ProviderAuditReport) string {
	modelIcons := map[string]string{"ok": "✅", "dead": "❌", "unknown": "⚠️"}
	keyIcons := map[string]string{
		"ok":      "✅",
		"missing": "❔",
		"invalid": "❌",
		"denied":  "⛔",
		"unknown": "⚠️",
	}
	lines := []string{"Kết quả audit API/model:"}
	for _, provider := range capabilityProviderOrder {
		providerReport, ok := report[provider]
		if !ok {
			continue
		}
		keyStatus := strings.TrimSpace(providerReport.Key.Status)
		if keyStatus == "" {
			keyStatus = "unknown"
		}
		keyReason := strings.TrimSpace(providerReport.Key.Reason)
		if keyReason == "" {
			keyReason = "unknown"
		}
		keyIcon := keyIcons[keyStatus]
		if keyIcon == "" {
			keyIcon = "⚠️"
		}
		lines = append(lines, "", fmt.Sprintf(
			"%s: %s key=%s",
			providerDisplayLabels[provider],
			keyIcon,
			keyReason,
		))

		for _, choice := range CandidateModelChoices[provider] {
			result, ok := providerReport.Models[choice.Model]
			status := "unknown"
			reason := "not_checked"
			if ok {
				if strings.TrimSpace(result.Status) != "" {
					status = strings.TrimSpace(result.Status)
				}
				if strings.TrimSpace(result.Reason) != "" {
					reason = strings.TrimSpace(result.Reason)
				}
			}
			icon := modelIcons[status]
			if icon == "" {
				icon = "⚠️"
			}
			httpText := ""
			if result.HTTPStatus != nil {
				httpText = fmt.Sprintf(" HTTP %d", *result.HTTPStatus)
			}
			lines = append(lines, fmt.Sprintf(
				"• %s %s: %s%s",
				icon,
				choice.Label,
				reason,
				httpText,
			))
		}
	}
	return truncateRunes(strings.Join(lines, "\n"), 3900)
}

func candidateLabel(provider, model string) string {
	for _, choice := range CandidateModelChoices[provider] {
		if choice.Model == model {
			return choice.Label
		}
	}
	return model
}

func AuditAlertText(events []AuditAlertEvent) string {
	lines := []string{"🔔 Thay đổi trạng thái API/model Atri:"}
	for _, event := range events {
		providerLabel := providerDisplayLabels[event.Provider]
		if providerLabel == "" {
			providerLabel = event.Provider
		}
		reason := strings.TrimSpace(event.Reason)
		if reason == "" {
			reason = "unknown"
		}
		httpText := ""
		if event.HTTPStatus != nil {
			httpText = fmt.Sprintf(" (HTTP %d)", *event.HTTPStatus)
		}
		modelLabel := candidateLabel(event.Provider, event.Model)

		switch event.Kind {
		case "key_failed":
			lines = append(lines, fmt.Sprintf("• ❌ %s key lỗi: %s%s", providerLabel, reason, httpText))
		case "key_recovered":
			lines = append(lines, fmt.Sprintf("• ✅ %s key đã phục hồi", providerLabel))
		case "model_dead":
			lines = append(lines, fmt.Sprintf("• ⛔ %s/%s chết: %s%s", providerLabel, modelLabel, reason, httpText))
		case "model_recovered":
			lines = append(lines, fmt.Sprintf("• ✅ %s/%s đã phục hồi", providerLabel, modelLabel))
		case "provider_all_dead":
			lines = append(lines, fmt.Sprintf("• 🚨 %s: toàn bộ model đã chết", providerLabel))
		case "provider_unavailable":
			lines = append(lines, fmt.Sprintf("• ⚠️ %s: tạm thời không khả dụng", providerLabel))
		case "provider_recovered":
			lines = append(lines, fmt.Sprintf("• ✅ %s: đã hoạt động lại", providerLabel))
		}
	}
	return truncateRunes(strings.Join(lines, "\n"), 3900)
}
