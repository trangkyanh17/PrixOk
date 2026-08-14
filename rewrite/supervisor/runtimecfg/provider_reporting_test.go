package runtimecfg

import (
	"strings"
	"testing"
)

func TestCompactAuditReportCountsStatuses(t *testing.T) {
	report := map[string]ProviderAuditReport{
		"cerebras": {
			Key: AuditResult{Status: "ok"},
			Models: map[string]AuditResult{
				"a": {Status: "ok"},
				"b": {Status: "dead"},
				"c": {Status: "weird"},
			},
		},
	}
	got := CompactAuditReport(report)
	want := "cerebras:key=ok,ok=1,dead=1,unknown=1"
	if got != want {
		t.Fatalf("got=%q want=%q", got, want)
	}
}

func TestAuditReportTextUsesCandidateLabelsAndHTTPStatus(t *testing.T) {
	status := 404
	report := map[string]ProviderAuditReport{
		"cerebras": {
			Key: AuditResult{Status: "ok", Reason: "key_valid"},
			Models: map[string]AuditResult{
				"gpt-oss-120b": {Status: "ok", Reason: "live_probe"},
				"zai-glm-4.7":  {Status: "dead", Reason: "model_not_available", HTTPStatus: &status},
			},
		},
	}
	text := AuditReportText(report)
	for _, expected := range []string{
		"Cerebras: ✅ key=key_valid",
		"• ✅ OSS120B: live_probe",
		"• ❌ GLM4.7-P: model_not_available HTTP 404",
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("missing %q in %q", expected, text)
		}
	}
}

func TestAuditAlertTextFormatsEvents(t *testing.T) {
	status := 401
	text := AuditAlertText([]AuditAlertEvent{
		{Kind: "key_failed", Provider: "groq", Reason: "key_invalid", HTTPStatus: &status},
		{Kind: "model_recovered", Provider: "openrouter", Model: "cohere/north-mini-code:free"},
		{Kind: "provider_all_dead", Provider: "cerebras"},
	})
	for _, expected := range []string{
		"• ❌ Groq key lỗi: key_invalid (HTTP 401)",
		"• ✅ OpenRouter/NORTH đã phục hồi",
		"• 🚨 Cerebras: toàn bộ model đã chết",
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("missing %q in %q", expected, text)
		}
	}
}

func TestTruncateRunesPreservesUnicodeBoundaries(t *testing.T) {
	got := truncateRunes("á🙂xyz", 2)
	if got != "á🙂" {
		t.Fatalf("got=%q", got)
	}
}
