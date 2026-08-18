package main

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestLoadConfigWatchdogObserveOnly(t *testing.T) {
	t.Setenv("ATRI_REWRITE_WATCHDOG", "true")
	t.Setenv("ATRI_REWRITE_WATCHDOG_OBSERVE_ONLY", "true")

	config := loadConfig()
	if !config.WatchdogEnabled {
		t.Fatal("watchdog should be enabled")
	}
	if !config.WatchdogObserveOnly {
		t.Fatal("watchdog observe-only should be enabled")
	}
}

func TestWatchdogObserveOnlySuppressesMutationsAndKeepsNetworkProbe(t *testing.T) {
	config := testWatchdogConfig()
	config.WatchdogObserveOnly = true

	checkKey := "tmux has-session -t prixok-bot"
	healthKey := config.LocalHealth + " --quiet"
	runner := &fakeWatchdogRunner{responses: map[string][]error{
		checkKey: {errors.New("missing tmux session")},
		fakeWatchdogCommandKey(newWatchdog(config, nil, nil, nil).botLockProbeCommand()): {fakeExitError(botLockFreeExitCode)},
		healthKey: {errors.New("unhealthy")},
	}}
	logs, logf := captureWatchdogLogs()
	watchdog := newWatchdog(config, runner, func(path string) bool {
		return path == config.BotLauncher || path == config.LocalHealth ||
			path == config.BrowserEnsure || path == config.NetworkState
	}, logf)

	watchdog.tick(context.Background())

	if runner.countPrefix("tmux new-session") != 0 {
		t.Fatal("observe-only watchdog attempted to restart the bot session")
	}
	if runner.countPrefix(config.BrowserEnsure+" --from-watchdog") != 0 {
		t.Fatal("observe-only watchdog attempted shared-component repair")
	}
	if runner.countPrefix(config.NetworkState+" --via-socks") != 1 {
		t.Fatalf("network probes=%d", runner.countPrefix(config.NetworkState+" --via-socks"))
	}

	joined := strings.Join(*logs, ",")
	for _, want := range []string{
		"WATCHDOG_OBSERVE_ONLY=ACTIVE",
		"BOT_SESSION_MISSING_OBSERVE_ONLY",
		"LOCAL_SHARED_COMPONENT_HEALTH=UNHEALTHY",
		"LOCAL_SHARED_COMPONENT_REPAIR=OBSERVE_ONLY",
		"NETWORK_STATE=ONLINE",
	} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing log %q in %v", want, *logs)
		}
	}
}

func TestWatchdogObserveOnlyLogsActivationOnce(t *testing.T) {
	config := testWatchdogConfig()
	config.WatchdogObserveOnly = true
	runner := &fakeWatchdogRunner{responses: map[string][]error{}}
	logs, logf := captureWatchdogLogs()
	watchdog := newWatchdog(config, runner, func(string) bool { return true }, logf)

	watchdog.tick(context.Background())
	watchdog.tick(context.Background())

	count := 0
	for _, line := range *logs {
		if line == "WATCHDOG_OBSERVE_ONLY=ACTIVE" {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("activation log count=%d logs=%v", count, *logs)
	}
}
