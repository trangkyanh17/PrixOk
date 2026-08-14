package main

import (
	"reflect"
	"testing"
	"time"
)

func TestDurationDefaults(t *testing.T) {
	if got := envDurationSeconds("ATRI_TEST_MISSING", 30); got != 30*time.Second {
		t.Fatalf("got %s", got)
	}
}

func TestLoadConfigMCPSettings(t *testing.T) {
	t.Setenv("ATRI_REWRITE_MCP_LIFECYCLE", "true")
	t.Setenv("ATRI_MCP_PREWARM_PLUGINS", " Serena,context7,serena , chrome-devtools ")
	t.Setenv("ATRI_MCP_PREWARM_CONCURRENCY", "4")
	t.Setenv("ATRI_MCP_PREWARM_TIMEOUT", "45")
	t.Setenv("ATRI_MCP_HEALTH_INTERVAL", "600")
	t.Setenv("ATRI_MCP_PRUNE_INTERVAL", "120")
	t.Setenv("ATRI_MCP_REQUEST_TIMEOUT", "150")
	t.Setenv("ATRI_MCP_IDLE_TTL", "2400")

	config := loadConfig()
	if !config.MCPLifecycleEnabled {
		t.Fatal("MCP lifecycle should be enabled")
	}
	if !reflect.DeepEqual(config.MCPPrewarmPlugins, []string{"serena", "context7", "chrome-devtools"}) {
		t.Fatalf("plugins=%v", config.MCPPrewarmPlugins)
	}
	if config.MCPPrewarmConcurrency != 4 {
		t.Fatalf("concurrency=%d", config.MCPPrewarmConcurrency)
	}
	if config.MCPPrewarmTimeout != 45*time.Second ||
		config.MCPHealthInterval != 600*time.Second ||
		config.MCPPruneInterval != 120*time.Second ||
		config.MCPRequestTimeout != 150*time.Second ||
		config.MCPIdleTTL != 2400*time.Second {
		t.Fatalf("unexpected MCP durations: %+v", config)
	}
}

func TestMCPConfigRejectsInvalidValues(t *testing.T) {
	t.Setenv("ATRI_REWRITE_MCP_LIFECYCLE", "not-a-bool")
	t.Setenv("ATRI_MCP_PREWARM_CONCURRENCY", "99")
	t.Setenv("ATRI_MCP_PREWARM_TIMEOUT", "0")
	config := loadConfig()
	if config.MCPLifecycleEnabled {
		t.Fatal("invalid bool should fall back to false")
	}
	if config.MCPPrewarmConcurrency != 2 {
		t.Fatalf("concurrency=%d", config.MCPPrewarmConcurrency)
	}
	if config.MCPPrewarmTimeout != 90*time.Second {
		t.Fatalf("timeout=%s", config.MCPPrewarmTimeout)
	}
}
