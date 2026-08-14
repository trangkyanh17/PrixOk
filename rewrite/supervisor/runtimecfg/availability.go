package runtimecfg

import "strings"

type ProviderModel struct {
	Provider string
	Model    string
}

func ModelStatus(provider, model string, status map[ProviderModel]string) string {
	provider = strings.ToLower(strings.TrimSpace(provider))
	if provider == "vertex" && model == "auto" {
		return "ok"
	}
	value := strings.ToLower(strings.TrimSpace(status[ProviderModel{Provider: provider, Model: model}]))
	switch value {
	case "ok", "dead", "unknown":
		return value
	default:
		return "unknown"
	}
}

func StatusIcon(provider, model string, status map[ProviderModel]string) string {
	switch ModelStatus(provider, model, status) {
	case "ok":
		return "✅"
	case "dead":
		return "⛔"
	default:
		return "❔"
	}
}

func FilterDeadModelChoices(provider string, choices []ModelChoice, status map[ProviderModel]string) []ModelChoice {
	provider = strings.ToLower(strings.TrimSpace(provider))
	out := make([]ModelChoice, 0, len(choices))
	for _, choice := range choices {
		if ModelStatus(provider, choice.Model, status) != "dead" {
			out = append(out, choice)
		}
	}
	if len(out) == 0 && provider == "vertex" {
		return []ModelChoice{{Model: "auto", Label: "AUTO"}}
	}
	return out
}

func ProviderHasLiveModel(provider string, choices []ModelChoice, status map[ProviderModel]string) bool {
	return len(FilterDeadModelChoices(provider, choices, status)) > 0
}

func HealSelectedModel(provider, selected, fallback string, choices []ModelChoice, status map[ProviderModel]string) string {
	visible := FilterDeadModelChoices(provider, choices, status)
	for _, choice := range visible {
		if choice.Model == selected {
			return selected
		}
	}
	for _, choice := range visible {
		if choice.Model == fallback {
			return fallback
		}
	}
	if len(visible) > 0 {
		return visible[0].Model
	}
	return fallback
}
