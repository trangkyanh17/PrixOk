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

func TestLoadConfigWatchdogSettings(t *testing.T) {
	t.Setenv("ATRI_REWRITE_WATCHDOG", "true")
	t.Setenv("ATRI_BOT_SESSION", "prixok-test")
	t.Setenv("ATRI_BOT_LAUNCHER", "/tmp/prixok-bot.sh")
	t.Setenv("ATRI_LOCAL_HEALTH", "/tmp/local-health.sh")
	t.Setenv("ATRI_BROWSER_ENSURE", "/tmp/browser-ensure.sh")
	t.Setenv("ATRI_NETWORK_STATE", "/tmp/network-state.sh")
	t.Setenv("ATRI_PROOT_DISTRO", "debian-test")
	t.Setenv("ATRI_BOT_LOCK_PATH", "/tmp/prixok.lock")
	t.Setenv("ATRI_WATCHDOG_INTERVAL", "12")
	t.Setenv("ATRI_WATCHDOG_COMMAND_TIMEOUT", "17")
	t.Setenv("ATRI_WATCHDOG_REPAIR_TIMEOUT", "88")
	t.Setenv("ATRI_NETWORK_INTERVAL", "44")
	t.Setenv("ATRI_NETWORK_TIMEOUT", "7")
	t.Setenv("ATRI_REWRITE_SHUTDOWN_TIMEOUT", "19")

	config := loadConfig()
	if !config.WatchdogEnabled {
		t.Fatal("watchdog should be enabled")
	}
	if config.BotSession != "prixok-test" || config.BotLauncher != "/tmp/prixok-bot.sh" ||
		config.LocalHealth != "/tmp/local-health.sh" || config.BrowserEnsure != "/tmp/browser-ensure.sh" ||
		config.NetworkState != "/tmp/network-state.sh" || config.ProotDistro != "debian-test" ||
		config.BotLockPath != "/tmp/prixok.lock" {
		t.Fatalf("unexpected watchdog paths: %+v", config)
	}
	if config.LoopInterval != 12*time.Second || config.CommandTimeout != 17*time.Second ||
		config.RepairTimeout != 88*time.Second || config.NetworkCheckInterval != 44*time.Second ||
		config.NetworkProbeTimeout != 7*time.Second || config.ShutdownTimeout != 19*time.Second {
		t.Fatalf("unexpected watchdog durations: %+v", config)
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
	t.Setenv("ATRI_MCP_REQUEST_TIMEOUT", "0")
	t.Setenv("ATRI_REWRITE_SHUTDOWN_TIMEOUT", "0")
	config := loadConfig()
	if config.MCPLifecycleEnabled {
		t.Fatal("invalid bool should fall back to false")
	}
	if config.MCPPrewarmConcurrency != 2 {
		t.Fatalf("concurrency=%d", config.MCPPrewarmConcurrency)
	}
	if config.MCPPrewarmTimeout != 240*time.Second {
		t.Fatalf("prewarm timeout=%s", config.MCPPrewarmTimeout)
	}
	if config.MCPRequestTimeout != 240*time.Second {
		t.Fatalf("request timeout=%s", config.MCPRequestTimeout)
	}
	if config.ShutdownTimeout != 15*time.Second {
		t.Fatalf("shutdown timeout=%s", config.ShutdownTimeout)
	}
}
