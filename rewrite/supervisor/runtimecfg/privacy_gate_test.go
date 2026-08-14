package runtimecfg

import (
	"strings"
	"testing"
)

func TestPublicTaskPrivacyGateAllowsOrdinaryPublicChat(t *testing.T) {
	allowed, reason := PublicTaskPrivacyGate("Giải thích thuật toán quicksort", "chat")
	if !allowed || reason != "public_safe" {
		t.Fatalf("allowed=%v reason=%q", allowed, reason)
	}
}

func TestPublicTaskPrivacyGateRejectsModesCommandsAndPrivateContext(t *testing.T) {
	cases := []struct {
		text   string
		mode   string
		reason string
	}{
		{"hello", "code", "mode_not_chat"},
		{"   ", "chat", "empty"},
		{"  /status", "chat", "command"},
		{"check repo của tôi", "chat", "private_phrase"},
		{"đọc /home/prix/secrets/x", "chat", "private_path"},
		{"-----BEGIN RSA PRIVATE KEY----- abc", "chat", "private_key"},
	}
	for _, test := range cases {
		allowed, reason := PublicTaskPrivacyGate(test.text, test.mode)
		if allowed || reason != test.reason {
			t.Fatalf("text=%q mode=%q allowed=%v reason=%q want=%q", test.text, test.mode, allowed, reason, test.reason)
		}
	}
}

func TestPublicTaskPrivacyGateRejectsSecretPatterns(t *testing.T) {
	cases := []string{
		"api_key=abcdefghijklmnop",
		"Authorization: Bearer abcdefghijklmnop",
		"token: abcdefghijklmnop",
		"OPENROUTER_API_KEY=abcdefghijklmnop",
		"sk-abcdefghijklmnop",
		"ghp_abcdefghijklmnop",
		"eyJabcdefghijk.abcdefghijk.abcdefghijk",
	}
	for _, text := range cases {
		allowed, reason := PublicTaskPrivacyGate(text, "chat")
		if allowed || reason != "secret_pattern" {
			t.Fatalf("text=%q allowed=%v reason=%q", text, allowed, reason)
		}
	}
}

func TestPublicTaskPrivacyGateRejectsPastedCodeSourceAndTraceback(t *testing.T) {
	cases := []struct {
		text   string
		reason string
	}{
		{"```python\nprint(1)\n```", "pasted_code"},
		{"Lỗi này:\nfrom pathlib import Path\nfoo", "pasted_source"},
		{"Traceback (most recent call last):\nValueError", "pasted_traceback"},
	}
	for _, test := range cases {
		allowed, reason := PublicTaskPrivacyGate(test.text, "chat")
		if allowed || reason != test.reason {
			t.Fatalf("text=%q allowed=%v reason=%q want=%q", test.text, allowed, reason, test.reason)
		}
	}
}

func TestPublicTaskPrivacyGateLongTextRequiresPublicMarker(t *testing.T) {
	privateUnknown := strings.Repeat("a", 6001)
	allowed, reason := PublicTaskPrivacyGate(privateUnknown, "chat")
	if allowed || reason != "long_text_unknown_privacy" {
		t.Fatalf("allowed=%v reason=%q", allowed, reason)
	}

	publicText := "documentation https://example.com " + strings.Repeat("a", 6001)
	allowed, reason = PublicTaskPrivacyGate(publicText, "chat")
	if !allowed || reason != "public_safe" {
		t.Fatalf("public allowed=%v reason=%q", allowed, reason)
	}
}
