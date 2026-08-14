package main

import (
	"os"
	"strconv"
	"strings"
	"time"
)

type config struct {
	WatchdogEnabled      bool
	WatchdogObserveOnly  bool
	BotSession           string
	BotLauncher          string
	LocalHealth          string
	BrowserEnsure        string
	NetworkState         string
	ProotDistro          string
	BotLockPath          string
	LoopInterval         time.Duration
	CommandTimeout       time.Duration
	RepairTimeout        time.Duration
	NetworkCheckInterval time.Duration
	NetworkProbeTimeout  time.Duration
	ShutdownTimeout      time.Duration

	MCPLifecycleEnabled   bool
	MCPPrewarmPlugins     []string
	MCPPrewarmConcurrency int
	MCPPrewarmTimeout     time.Duration
	MCPHealthInterval     time.Duration
	MCPPruneInterval      time.Duration
	MCPRequestTimeout     time.Duration
	MCPIdleTTL            time.Duration
}

func loadConfig() config {
	return config{
		WatchdogEnabled:      envBool("ATRI_REWRITE_WATCHDOG", false),
		WatchdogObserveOnly:  envBool("ATRI_REWRITE_WATCHDOG_OBSERVE_ONLY", false),
		BotSession:           envString("ATRI_BOT_SESSION", "prixok-bot"),
		BotLauncher:          envString("ATRI_BOT_LAUNCHER", os.ExpandEnv("$HOME/prixok-bot.sh")),
		LocalHealth:          envString("ATRI_LOCAL_HEALTH", os.ExpandEnv("$HOME/atri-production-local-health.sh")),
		BrowserEnsure:        envString("ATRI_BROWSER_ENSURE", os.ExpandEnv("$HOME/atri-production-browser-ensure.sh")),
		NetworkState:         envString("ATRI_NETWORK_STATE", os.ExpandEnv("$HOME/atri-production-network-state.sh")),
		ProotDistro:          envString("ATRI_PROOT_DISTRO", "debian"),
		BotLockPath:          envString("ATRI_BOT_LOCK_PATH", "/app/.atri-prixok-bot-v133.lock"),
		LoopInterval:         envDurationSeconds("ATRI_WATCHDOG_INTERVAL", 30),
		CommandTimeout:       envDurationSeconds("ATRI_WATCHDOG_COMMAND_TIMEOUT", 30),
		RepairTimeout:        envDurationSeconds("ATRI_WATCHDOG_REPAIR_TIMEOUT", 270),
		NetworkCheckInterval: envDurationSeconds("ATRI_NETWORK_INTERVAL", 180),
		NetworkProbeTimeout:  envDurationSeconds("ATRI_NETWORK_TIMEOUT", 8),
		ShutdownTimeout:      envDurationSeconds("ATRI_REWRITE_SHUTDOWN_TIMEOUT", 15),

		MCPLifecycleEnabled: envBool("ATRI_REWRITE_MCP_LIFECYCLE", false),
		MCPPrewarmPlugins: envCSV("ATRI_MCP_PREWARM_PLUGINS", []string{
			"serena",
			"context7",
			"github",
			"semgrep",
			"sentry",
			"chrome-devtools",
		}),
		MCPPrewarmConcurrency: envIntRange("ATRI_MCP_PREWARM_CONCURRENCY", 2, 1, 16),
		MCPPrewarmTimeout:     envDurationSeconds("ATRI_MCP_PREWARM_TIMEOUT", 240),
		MCPHealthInterval:     envDurationSeconds("ATRI_MCP_HEALTH_INTERVAL", 1800),
		MCPPruneInterval:      envDurationSeconds("ATRI_MCP_PRUNE_INTERVAL", 300),
		MCPRequestTimeout:     envDurationSeconds("ATRI_MCP_REQUEST_TIMEOUT", 240),
		MCPIdleTTL:            envDurationSeconds("ATRI_MCP_IDLE_TTL", 3600),
	}
}

func envString(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func envDurationSeconds(name string, fallback int) time.Duration {
	value, err := strconv.Atoi(os.Getenv(name))
	if err != nil || value <= 0 {
		value = fallback
	}
	return time.Duration(value) * time.Second
}

func envBool(name string, fallback bool) bool {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(value)
	if err != nil {
		return fallback
	}
	return parsed
}

func envIntRange(name string, fallback, minimum, maximum int) int {
	value, err := strconv.Atoi(strings.TrimSpace(os.Getenv(name)))
	if err != nil || value < minimum || value > maximum {
		return fallback
	}
	return value
}

func envCSV(name string, fallback []string) []string {
	raw, ok := os.LookupEnv(name)
	if !ok {
		return append([]string(nil), fallback...)
	}
	seen := map[string]struct{}{}
	values := make([]string, 0)
	for _, item := range strings.Split(raw, ",") {
		item = strings.ToLower(strings.TrimSpace(item))
		if item == "" {
			continue
		}
		if _, exists := seen[item]; exists {
			continue
		}
		seen[item] = struct{}{}
		values = append(values, item)
	}
	if len(values) == 0 {
		return append([]string(nil), fallback...)
	}
	return values
}
