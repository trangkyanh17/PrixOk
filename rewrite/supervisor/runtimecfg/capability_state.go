package runtimecfg

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type ModelCapabilityRecord struct {
	Status     string `json:"status"`
	Reason     string `json:"reason"`
	HTTPStatus *int   `json:"http_status"`
	CheckedAt  int64  `json:"checked_at"`
}

type AuditResult struct {
	Status     string `json:"status"`
	Reason     string `json:"reason"`
	HTTPStatus *int   `json:"http_status"`
}

type ProviderAuditReport struct {
	Key    AuditResult            `json:"key"`
	Models map[string]AuditResult `json:"models"`
}

type ProviderAlertSnapshot struct {
	Key            AuditResult            `json:"key"`
	Models         map[string]AuditResult `json:"models"`
	ProviderStatus string                 `json:"provider_status"`
}

type AuditAlertEvent struct {
	Kind       string
	Provider   string
	Model      string
	Status     string
	Reason     string
	HTTPStatus *int
}

type CapabilityState struct {
	Version       int                                         `json:"version"`
	UpdatedAt     int64                                       `json:"updated_at"`
	LastAuditAt   int64                                       `json:"last_audit_at"`
	Models        map[string]map[string]ModelCapabilityRecord `json:"models"`
	Discovered    map[string][]string                         `json:"discovered"`
	AlertSnapshot map[string]ProviderAlertSnapshot            `json:"alert_snapshot"`
}

var capabilityProviderOrder = []string{"cerebras", "groq", "openrouter", "vertex"}

func BlankCapabilityState() CapabilityState {
	return CapabilityState{
		Version:       2,
		Models:        map[string]map[string]ModelCapabilityRecord{},
		Discovered:    map[string][]string{},
		AlertSnapshot: map[string]ProviderAlertSnapshot{},
	}
}

func (state *CapabilityState) normalize() {
	if state.Version == 0 {
		state.Version = 2
	}
	if state.Models == nil {
		state.Models = map[string]map[string]ModelCapabilityRecord{}
	}
	if state.Discovered == nil {
		state.Discovered = map[string][]string{}
	}
	if state.AlertSnapshot == nil {
		state.AlertSnapshot = map[string]ProviderAlertSnapshot{}
	}
}

func LoadCapabilityState(path string) CapabilityState {
	state := BlankCapabilityState()
	data, err := os.ReadFile(path)
	if err != nil {
		return state
	}
	if err := json.Unmarshal(data, &state); err != nil {
		return BlankCapabilityState()
	}
	state.normalize()
	return state
}

func SaveCapabilityState(path string, state *CapabilityState, now int64) error {
	state.normalize()
	if now <= 0 {
		now = time.Now().Unix()
	}
	state.UpdatedAt = now

	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	payload, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	payload = append(payload, '\n')

	file, err := os.CreateTemp(dir, ".atri-provider-capabilities-*.json")
	if err != nil {
		return err
	}
	tmp := file.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(tmp)
		}
	}()

	if err := file.Chmod(0o600); err != nil {
		_ = file.Close()
		return err
	}
	if _, err := file.Write(payload); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	if err := os.Chmod(path, 0o600); err != nil {
		return err
	}
	cleanup = false
	return nil
}

func CapabilityAuditAgeSeconds(state CapabilityState, now int64) float64 {
	if state.LastAuditAt <= 0 {
		return math.Inf(1)
	}
	if now <= 0 {
		now = time.Now().Unix()
	}
	if now <= state.LastAuditAt {
		return 0
	}
	return float64(now - state.LastAuditAt)
}

func (state *CapabilityState) providerBucket(provider string) map[string]ModelCapabilityRecord {
	state.normalize()
	provider = strings.ToLower(strings.TrimSpace(provider))
	bucket := state.Models[provider]
	if bucket == nil {
		bucket = map[string]ModelCapabilityRecord{}
		state.Models[provider] = bucket
	}
	return bucket
}

func (state CapabilityState) ModelRecord(provider, model string) ModelCapabilityRecord {
	provider = strings.ToLower(strings.TrimSpace(provider))
	if bucket := state.Models[provider]; bucket != nil {
		return bucket[model]
	}
	return ModelCapabilityRecord{}
}

func (state CapabilityState) CapabilityModelStatus(provider, model string) string {
	provider = strings.ToLower(strings.TrimSpace(provider))
	if provider == "vertex" && model == "auto" {
		return "ok"
	}
	status := strings.ToLower(strings.TrimSpace(state.ModelRecord(provider, model).Status))
	switch status {
	case "ok", "dead", "unknown":
		return status
	default:
		return "unknown"
	}
}

func (state CapabilityState) CapabilityStatusIcon(provider, model string) string {
	switch state.CapabilityModelStatus(provider, model) {
	case "ok":
		return "✅"
	case "dead":
		return "⛔"
	default:
		return "❔"
	}
}

func (state *CapabilityState) SetModelRecord(provider, model, status, reason string, httpStatus *int, now int64) {
	if now <= 0 {
		now = time.Now().Unix()
	}
	reasonRunes := []rune(reason)
	if len(reasonRunes) > 240 {
		reason = string(reasonRunes[:240])
	}
	state.providerBucket(provider)[model] = ModelCapabilityRecord{
		Status:     status,
		Reason:     reason,
		HTTPStatus: httpStatus,
		CheckedAt:  now,
	}
}

func (state *CapabilityState) MarkModelUnavailable(provider, model, reason string, now int64) {
	state.SetModelRecord(provider, model, "dead", reason, nil, now)
}

func (state *CapabilityState) MarkModelAvailable(provider, model, reason string, now int64) {
	state.SetModelRecord(provider, model, "ok", reason, nil, now)
}

func keyHealth(status string) string {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "ok":
		return "ok"
	case "missing", "invalid", "denied":
		return "bad"
	default:
		return "transient"
	}
}

func normalizeAuditResult(result AuditResult) AuditResult {
	if strings.TrimSpace(result.Status) == "" {
		result.Status = "unknown"
	}
	if strings.TrimSpace(result.Reason) == "" {
		result.Reason = "unknown"
	}
	return result
}

func BuildAuditAlertSnapshot(report map[string]ProviderAuditReport) map[string]ProviderAlertSnapshot {
	snapshot := map[string]ProviderAlertSnapshot{}
	for _, provider := range capabilityProviderOrder {
		providerReport, ok := report[provider]
		if !ok {
			continue
		}
		key := normalizeAuditResult(providerReport.Key)
		models := map[string]AuditResult{}
		statuses := make([]string, 0, len(providerReport.Models))
		for model, result := range providerReport.Models {
			result = normalizeAuditResult(result)
			models[model] = result
			statuses = append(statuses, result.Status)
		}

		providerStatus := "unavailable"
		if keyHealth(key.Status) == "bad" {
			providerStatus = "key_bad"
		} else if containsStatus(statuses, "ok") {
			providerStatus = "healthy"
		} else if len(statuses) > 0 && allStatus(statuses, "dead") {
			providerStatus = "all_dead"
		}
		snapshot[provider] = ProviderAlertSnapshot{
			Key:            key,
			Models:         models,
			ProviderStatus: providerStatus,
		}
	}
	return snapshot
}

func containsStatus(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func allStatus(values []string, target string) bool {
	for _, value := range values {
		if value != target {
			return false
		}
	}
	return true
}

func cloneAlertSnapshot(input map[string]ProviderAlertSnapshot) map[string]ProviderAlertSnapshot {
	output := make(map[string]ProviderAlertSnapshot, len(input))
	for provider, item := range input {
		models := make(map[string]AuditResult, len(item.Models))
		for model, result := range item.Models {
			models[model] = result
		}
		item.Models = models
		output[provider] = item
	}
	return output
}

func (state CapabilityState) CurrentAuditAlertSnapshot() map[string]ProviderAlertSnapshot {
	if len(state.AlertSnapshot) > 0 {
		return cloneAlertSnapshot(state.AlertSnapshot)
	}
	legacy := map[string]ProviderAuditReport{}
	for provider, choices := range CandidateModelChoices {
		models := map[string]AuditResult{}
		for _, choice := range choices {
			record := state.ModelRecord(provider, choice.Model)
			models[choice.Model] = AuditResult{
				Status:     record.Status,
				Reason:     record.Reason,
				HTTPStatus: record.HTTPStatus,
			}
		}
		legacy[provider] = ProviderAuditReport{
			Key:    AuditResult{Status: "unknown", Reason: "baseline_not_audited"},
			Models: models,
		}
	}
	return BuildAuditAlertSnapshot(legacy)
}

func AuditAlertEvents(report map[string]ProviderAuditReport, previous map[string]ProviderAlertSnapshot) []AuditAlertEvent {
	current := BuildAuditAlertSnapshot(report)
	events := []AuditAlertEvent{}
	for _, provider := range capabilityProviderOrder {
		before, beforeOK := previous[provider]
		after, afterOK := current[provider]
		if !beforeOK || !afterOK {
			continue
		}

		beforeKeyHealth := keyHealth(before.Key.Status)
		afterKeyHealth := keyHealth(after.Key.Status)
		if afterKeyHealth == "bad" && beforeKeyHealth != "bad" {
			events = append(events, AuditAlertEvent{Kind: "key_failed", Provider: provider, Status: after.Key.Status, Reason: after.Key.Reason, HTTPStatus: after.Key.HTTPStatus})
		} else if beforeKeyHealth == "bad" && afterKeyHealth == "ok" {
			events = append(events, AuditAlertEvent{Kind: "key_recovered", Provider: provider, Status: after.Key.Status, Reason: after.Key.Reason, HTTPStatus: after.Key.HTTPStatus})
		}

		for model, afterModel := range after.Models {
			beforeModel, ok := before.Models[model]
			if !ok {
				continue
			}
			if afterModel.Status == "dead" && beforeModel.Status != "dead" {
				events = append(events, AuditAlertEvent{Kind: "model_dead", Provider: provider, Model: model, Status: afterModel.Status, Reason: afterModel.Reason, HTTPStatus: afterModel.HTTPStatus})
			} else if beforeModel.Status == "dead" && afterModel.Status == "ok" {
				events = append(events, AuditAlertEvent{Kind: "model_recovered", Provider: provider, Model: model, Status: afterModel.Status, Reason: afterModel.Reason, HTTPStatus: afterModel.HTTPStatus})
			}
		}

		switch {
		case after.ProviderStatus == "all_dead" && before.ProviderStatus != "all_dead":
			events = append(events, AuditAlertEvent{Kind: "provider_all_dead", Provider: provider})
		case after.ProviderStatus == "unavailable" && before.ProviderStatus == "healthy":
			events = append(events, AuditAlertEvent{Kind: "provider_unavailable", Provider: provider})
		case after.ProviderStatus == "healthy" && (before.ProviderStatus == "all_dead" || before.ProviderStatus == "unavailable"):
			events = append(events, AuditAlertEvent{Kind: "provider_recovered", Provider: provider})
		}
	}
	return events
}

func (state *CapabilityState) CommitAuditAlertSnapshot(report map[string]ProviderAuditReport) {
	state.normalize()
	state.AlertSnapshot = BuildAuditAlertSnapshot(report)
}
