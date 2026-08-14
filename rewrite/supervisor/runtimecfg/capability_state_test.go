package runtimecfg

import (
	"math"
	"os"
	"path/filepath"
	"testing"
)

func intPointer(value int) *int {
	copyValue := value
	return &copyValue
}

func hasAuditEvent(events []AuditAlertEvent, kind, provider, model string) bool {
	for _, event := range events {
		if event.Kind == kind && event.Provider == provider && event.Model == model {
			return true
		}
	}
	return false
}

func TestCapabilityStateMissingFileUsesBlankV2(t *testing.T) {
	state := LoadCapabilityState(filepath.Join(t.TempDir(), "missing.json"))
	if state.Version != 2 || state.Models == nil || state.Discovered == nil || state.AlertSnapshot == nil {
		t.Fatalf("state=%+v", state)
	}
	if !math.IsInf(CapabilityAuditAgeSeconds(state, 100), 1) {
		t.Fatal("never-audited state should report infinite age")
	}
}

func TestCapabilityStateAtomicRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "capabilities.json")
	state := BlankCapabilityState()
	status := 404
	state.SetModelRecord("Groq", "model-x", "dead", "model_not_available", &status, 90)
	state.LastAuditAt = 95
	state.Discovered["groq"] = []string{"model-x", "model-y"}

	if err := SaveCapabilityState(path, &state, 100); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != 0o600 {
		t.Fatalf("mode=%o want=600", got)
	}

	loaded := LoadCapabilityState(path)
	if loaded.UpdatedAt != 100 || loaded.LastAuditAt != 95 {
		t.Fatalf("loaded=%+v", loaded)
	}
	record := loaded.ModelRecord("groq", "model-x")
	if record.Status != "dead" || record.HTTPStatus == nil || *record.HTTPStatus != 404 {
		t.Fatalf("record=%+v", record)
	}
	if age := CapabilityAuditAgeSeconds(loaded, 110); age != 15 {
		t.Fatalf("age=%v want=15", age)
	}
}

func TestCapabilityModelStatusAndMarks(t *testing.T) {
	state := BlankCapabilityState()
	if got := state.CapabilityModelStatus("vertex", "auto"); got != "ok" {
		t.Fatalf("vertex auto=%q", got)
	}
	state.MarkModelUnavailable("groq", "model-x", "gone", 100)
	if got := state.CapabilityModelStatus("groq", "model-x"); got != "dead" {
		t.Fatalf("status=%q", got)
	}
	if got := state.CapabilityStatusIcon("groq", "model-x"); got != "⛔" {
		t.Fatalf("icon=%q", got)
	}
	state.MarkModelAvailable("groq", "model-x", "live_probe", 101)
	if got := state.CapabilityModelStatus("groq", "model-x"); got != "ok" {
		t.Fatalf("status=%q", got)
	}
}

func TestAuditAlertEventsDetectModelAndProviderFailure(t *testing.T) {
	previousReport := map[string]ProviderAuditReport{
		"groq": {
			Key: AuditResult{Status: "ok", Reason: "key_valid", HTTPStatus: intPointer(200)},
			Models: map[string]AuditResult{
				"model-x": {Status: "ok", Reason: "live_probe", HTTPStatus: intPointer(200)},
			},
		},
	}
	currentReport := map[string]ProviderAuditReport{
		"groq": {
			Key: AuditResult{Status: "ok", Reason: "key_valid", HTTPStatus: intPointer(200)},
			Models: map[string]AuditResult{
				"model-x": {Status: "dead", Reason: "model_not_available", HTTPStatus: intPointer(404)},
			},
		},
	}

	events := AuditAlertEvents(currentReport, BuildAuditAlertSnapshot(previousReport))
	if !hasAuditEvent(events, "model_dead", "groq", "model-x") {
		t.Fatalf("events=%+v", events)
	}
	if !hasAuditEvent(events, "provider_all_dead", "groq", "") {
		t.Fatalf("events=%+v", events)
	}
}

func TestAuditAlertEventsDetectKeyRecovery(t *testing.T) {
	previous := BuildAuditAlertSnapshot(map[string]ProviderAuditReport{
		"cerebras": {
			Key:    AuditResult{Status: "invalid", Reason: "key_invalid", HTTPStatus: intPointer(401)},
			Models: map[string]AuditResult{},
		},
	})
	current := map[string]ProviderAuditReport{
		"cerebras": {
			Key:    AuditResult{Status: "ok", Reason: "key_valid", HTTPStatus: intPointer(200)},
			Models: map[string]AuditResult{},
		},
	}
	events := AuditAlertEvents(current, previous)
	if !hasAuditEvent(events, "key_recovered", "cerebras", "") {
		t.Fatalf("events=%+v", events)
	}
}

func TestCommitAuditAlertSnapshotCopiesReportState(t *testing.T) {
	state := BlankCapabilityState()
	report := map[string]ProviderAuditReport{
		"groq": {
			Key: AuditResult{Status: "ok", Reason: "key_valid"},
			Models: map[string]AuditResult{
				"model-x": {Status: "ok", Reason: "live_probe"},
			},
		},
	}
	state.CommitAuditAlertSnapshot(report)
	current := state.CurrentAuditAlertSnapshot()
	item := current["groq"]
	if item.ProviderStatus != "healthy" {
		t.Fatalf("snapshot=%+v", item)
	}
	item.Models["model-x"] = AuditResult{Status: "dead"}
	if state.AlertSnapshot["groq"].Models["model-x"].Status != "ok" {
		t.Fatal("returned snapshot aliases stored state")
	}
}
