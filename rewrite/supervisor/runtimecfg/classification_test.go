package runtimecfg

import "testing"

func TestTerminalModelErrors(t *testing.T) {
	if !IsTerminalModelError(404, true, "") {
		t.Fatal("404 should be terminal")
	}
	if !IsTerminalModelError(400, true, "unknown model requested") {
		t.Fatal("unknown model should be terminal")
	}
	if IsTerminalModelError(429, true, "unknown model requested") {
		t.Fatal("rate limit should not be terminal")
	}
}

func TestProbeClassification(t *testing.T) {
	status, reason := ClassifyProbe(429, true, "")
	if status != "unknown" || reason != "rate_limited" {
		t.Fatalf("status=%q reason=%q", status, reason)
	}
	status, reason = ClassifyProbe(410, true, "")
	if status != "dead" || reason != "model_not_available" {
		t.Fatalf("status=%q reason=%q", status, reason)
	}
}

func TestKeyClassification(t *testing.T) {
	status, reason := ClassifyKeyCheck(401, true)
	if status != "invalid" || reason != "key_invalid" {
		t.Fatalf("status=%q reason=%q", status, reason)
	}
	status, reason = ClassifyKeyCheck(0, false)
	if status != "unknown" || reason != "network_error" {
		t.Fatalf("status=%q reason=%q", status, reason)
	}
}
