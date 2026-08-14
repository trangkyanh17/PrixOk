package runtimecfg

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func tempRuntimeConfig(t *testing.T, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.py")
	if err := os.WriteFile(path, []byte(content), 0o640); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestRuntimeStateForFallsBackAndHealsThinking(t *testing.T) {
	state := RuntimeStateFor("missing", "impossible")
	if state.Model != "gemini-3.5-flash-lite" || state.Thinking != "minimal" {
		t.Fatalf("unexpected fallback state: %#v", state)
	}
}

func TestSetRuntimeModelResolvesAliasAndResetsThinking(t *testing.T) {
	path := tempRuntimeConfig(t, "VERTEX_MODEL = 'old'\nVERTEX_THINKING_LEVEL = 'low'\n")
	state, err := SetRuntimeModel(path, "flash")
	if err != nil {
		t.Fatal(err)
	}
	if state.Model != "gemini-3.6-flash" || state.Thinking != "medium" {
		t.Fatalf("unexpected state: %#v", state)
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(payload)
	if !strings.Contains(text, "VERTEX_MODEL = 'gemini-3.6-flash'") {
		t.Fatalf("model not persisted: %s", text)
	}
	if !strings.Contains(text, "VERTEX_THINKING_LEVEL = 'medium'") {
		t.Fatalf("thinking not reset: %s", text)
	}
}

func TestSetRuntimeThinkingUsesModelRules(t *testing.T) {
	path := tempRuntimeConfig(t, "VERTEX_MODEL = 'gemini-3.1-pro-preview'\n")
	if _, err := SetRuntimeThinking(path, "gemini-3.1-pro-preview", "minimal"); err == nil {
		t.Fatal("expected unsupported thinking error")
	}
	if _, err := SetRuntimeThinking(path, "gemini-3.1-pro-preview", ""); err == nil {
		t.Fatal("empty thinking must be rejected by setter")
	}
	state, err := SetRuntimeThinking(path, "gemini-3.1-pro-preview", "default")
	if err != nil {
		t.Fatal(err)
	}
	if state.Thinking != "high" {
		t.Fatalf("expected high default, got %s", state.Thinking)
	}
}

func TestWriteConfigValuesCollapsesDuplicatesAndPreservesMode(t *testing.T) {
	path := tempRuntimeConfig(t, "X = 1\nVERTEX_MODEL = 'a'\nVERTEX_MODEL = 'b'\n")
	if err := os.Chmod(path, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := writeConfigValues(path, map[string]string{
		"VERTEX_THINKING_LEVEL": "high",
		"VERTEX_MODEL":          "gemini-3-flash-preview",
	}); err != nil {
		t.Fatal(err)
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(payload)
	if strings.Count(text, "VERTEX_MODEL =") != 1 {
		t.Fatalf("duplicate model assignments remain: %s", payload)
	}
	if strings.Index(text, "VERTEX_MODEL =") > strings.Index(text, "VERTEX_THINKING_LEVEL =") {
		t.Fatalf("runtime keys persisted out of Python order: %s", text)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("mode changed: %o", info.Mode().Perm())
	}
}
