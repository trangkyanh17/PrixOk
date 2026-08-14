package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPrepareMCPEnvironmentForSerena(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CACHE_HOME", "")
	t.Setenv("PATH", "/usr/bin")
	t.Setenv("ATRI_UVX", "/app/mltbenv/bin/uvx")
	t.Setenv("ATRI_MCP_UV_LINK_MODE", "")
	t.Setenv("UV_LINK_MODE", "")
	t.Setenv("ATRI_MCP_UV_CACHE_DIR", "")
	t.Setenv("UV_CACHE_DIR", "")

	if err := prepareMCPEnvironment([]string{"serena"}); err != nil {
		t.Fatal(err)
	}
	parts := filepath.SplitList(os.Getenv("PATH"))
	if len(parts) == 0 || parts[0] != "/app/mltbenv/bin" {
		t.Fatalf("PATH=%q", os.Getenv("PATH"))
	}
	if os.Getenv("UV_LINK_MODE") != "copy" {
		t.Fatalf("UV_LINK_MODE=%q", os.Getenv("UV_LINK_MODE"))
	}
	expectedCache := filepath.Join(home, ".cache", "atri-rewrite-v150", "uv")
	if os.Getenv("UV_CACHE_DIR") != expectedCache {
		t.Fatalf("UV_CACHE_DIR=%q want=%q", os.Getenv("UV_CACHE_DIR"), expectedCache)
	}
	if info, err := os.Stat(expectedCache); err != nil || !info.IsDir() {
		t.Fatalf("cache dir missing: info=%v err=%v", info, err)
	}
}

func TestPrepareMCPEnvironmentRespectsOverrides(t *testing.T) {
	cache := filepath.Join(t.TempDir(), "uv-cache")
	t.Setenv("PATH", "/app/mltbenv/bin:/usr/bin")
	t.Setenv("ATRI_UVX", "/app/mltbenv/bin/uvx")
	t.Setenv("ATRI_MCP_UV_LINK_MODE", "hardlink")
	t.Setenv("UV_LINK_MODE", "copy")
	t.Setenv("ATRI_MCP_UV_CACHE_DIR", cache)
	t.Setenv("UV_CACHE_DIR", "/tmp/ignored")

	if err := prepareMCPEnvironment([]string{"semgrep"}); err != nil {
		t.Fatal(err)
	}
	if os.Getenv("UV_LINK_MODE") != "hardlink" {
		t.Fatalf("UV_LINK_MODE=%q", os.Getenv("UV_LINK_MODE"))
	}
	if os.Getenv("UV_CACHE_DIR") != cache {
		t.Fatalf("UV_CACHE_DIR=%q", os.Getenv("UV_CACHE_DIR"))
	}
	if strings.Count(os.Getenv("PATH"), "/app/mltbenv/bin") != 1 {
		t.Fatalf("PATH duplicated uvx directory: %q", os.Getenv("PATH"))
	}
}

func TestPrepareMCPEnvironmentLeavesHTTPOnlyLifecycleAlone(t *testing.T) {
	t.Setenv("PATH", "/usr/bin")
	t.Setenv("UV_LINK_MODE", "sentinel")
	t.Setenv("UV_CACHE_DIR", "/tmp/sentinel")

	if err := prepareMCPEnvironment([]string{"context7", "github"}); err != nil {
		t.Fatal(err)
	}
	if os.Getenv("PATH") != "/usr/bin" || os.Getenv("UV_LINK_MODE") != "sentinel" || os.Getenv("UV_CACHE_DIR") != "/tmp/sentinel" {
		t.Fatalf("HTTP-only lifecycle mutated environment: PATH=%q link=%q cache=%q", os.Getenv("PATH"), os.Getenv("UV_LINK_MODE"), os.Getenv("UV_CACHE_DIR"))
	}
}
