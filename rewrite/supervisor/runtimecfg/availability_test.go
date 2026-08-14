package runtimecfg

import "testing"

func TestVertexAutoIsAlwaysAvailable(t *testing.T) {
	status := map[ProviderModel]string{{Provider: "vertex", Model: "auto"}: "dead"}
	if got := ModelStatus("vertex", "auto", status); got != "ok" {
		t.Fatalf("status=%q", got)
	}
	if got := StatusIcon("vertex", "auto", status); got != "✅" {
		t.Fatalf("icon=%q", got)
	}
}

func TestFilterDeadChoicesAndHealSelection(t *testing.T) {
	choices := []ModelChoice{
		{Model: "a", Label: "A"},
		{Model: "b", Label: "B"},
	}
	status := map[ProviderModel]string{
		{Provider: "groq", Model: "a"}: "dead",
	}
	visible := FilterDeadModelChoices("groq", choices, status)
	if len(visible) != 1 || visible[0].Model != "b" {
		t.Fatalf("visible=%v", visible)
	}
	if got := HealSelectedModel("groq", "a", "b", choices, status); got != "b" {
		t.Fatalf("healed=%q", got)
	}
	if !ProviderHasLiveModel("groq", choices, status) {
		t.Fatal("expected provider to retain one visible model")
	}
}

func TestVertexChoiceFallbackNeverBecomesEmpty(t *testing.T) {
	choices := []ModelChoice{{Model: "dead", Label: "DEAD"}}
	status := map[ProviderModel]string{{Provider: "vertex", Model: "dead"}: "dead"}
	visible := FilterDeadModelChoices("vertex", choices, status)
	if len(visible) != 1 || visible[0].Model != "auto" {
		t.Fatalf("visible=%v", visible)
	}
}
