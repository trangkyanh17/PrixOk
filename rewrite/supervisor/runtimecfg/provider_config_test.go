package runtimecfg

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestProviderEnvPath(t *testing.T) {
	if got := ProviderEnvPath(""); got != DefaultProviderEnvPath {
		t.Fatalf("default path=%q", got)
	}
	if got := ProviderEnvPath(" /tmp/providers.env "); got != "/tmp/providers.env" {
		t.Fatalf("configured path=%q", got)
	}
}

func TestProviderConfigParsesAndOverlaysEnvironment(t *testing.T) {
	path := filepath.Join(t.TempDir(), "providers.env")
	payload := "# comment\nCEREBRAS_API_KEY='file-c'\nGROQ_API_KEY=\"file-g\"\nOTHER=value\nATRI_FREE_MAX_TOKENS=2048\n"
	if err := os.WriteFile(path, []byte(payload), 0o600); err != nil {
		t.Fatal(err)
	}
	cache := NewProviderConfigCache()
	values := cache.Load(path, map[string]string{
		"GROQ_API_KEY":       "env-g",
		"OPENROUTER_API_KEY": "env-o",
		"UNRELATED":          "ignored",
	})
	if values["CEREBRAS_API_KEY"] != "file-c" || values["GROQ_API_KEY"] != "env-g" || values["OPENROUTER_API_KEY"] != "env-o" {
		t.Fatalf("values=%v", values)
	}
	if values["OTHER"] != "value" {
		t.Fatalf("file value missing: %v", values)
	}
	if _, ok := values["UNRELATED"]; ok {
		t.Fatalf("unrelated environment leaked: %v", values)
	}
}

func TestProviderConfigCacheRefreshesOnMtime(t *testing.T) {
	path := filepath.Join(t.TempDir(), "providers.env")
	if err := os.WriteFile(path, []byte("GROQ_API_KEY=one\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cache := NewProviderConfigCache()
	if got := cache.Load(path, nil)["GROQ_API_KEY"]; got != "one" {
		t.Fatalf("first=%q", got)
	}

	next := time.Now().Add(2 * time.Second)
	if err := os.WriteFile(path, []byte("GROQ_API_KEY=two\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, next, next); err != nil {
		t.Fatal(err)
	}
	if got := cache.Load(path, nil)["GROQ_API_KEY"]; got != "two" {
		t.Fatalf("second=%q", got)
	}
}

func TestProviderAPIKeys(t *testing.T) {
	keys := ProviderAPIKeys(map[string]string{
		"CEREBRAS_API_KEY": " c ",
		"GROQ_API_KEY":     "g",
	})
	if keys["cerebras"] != "c" || keys["groq"] != "g" || keys["openrouter"] != "" {
		t.Fatalf("keys=%v", keys)
	}
}

func TestProviderConfigResetClearsCache(t *testing.T) {
	path := filepath.Join(t.TempDir(), "providers.env")
	if err := os.WriteFile(path, []byte("GROQ_API_KEY=one\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	cache := NewProviderConfigCache()
	_ = cache.Load(path, nil)
	cache.Reset()
	if cache.path != "" || cache.mtimeNS != -1 || len(cache.values) != 0 {
		t.Fatalf("cache=%+v", cache)
	}
}
