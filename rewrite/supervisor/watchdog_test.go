package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
)

func fakeWatchdogCommandKey(command watchdogCommand) string {
	return strings.TrimSpace(fmt.Sprintf("%s %s", command.Path, strings.Join(command.Args, " ")))
}

type fakeWatchdogRunner struct {
	mu        sync.Mutex
	calls     []watchdogCommand
	responses map[string][]error
}

func (runner *fakeWatchdogRunner) Run(_ context.Context, command watchdogCommand) error {
	runner.mu.Lock()
	defer runner.mu.Unlock()
	runner.calls = append(runner.calls, command)
	key := fakeWatchdogCommandKey(command)
	queue := runner.responses[key]
	if len(queue) == 0 {
		return nil
	}
	err := queue[0]
	runner.responses[key] = queue[1:]
	return err
}

func (runner *fakeWatchdogRunner) countPrefix(prefix string) int {
	runner.mu.Lock()
	defer runner.mu.Unlock()
	count := 0
	for _, command := range runner.calls {
		if strings.HasPrefix(fakeWatchdogCommandKey(command), prefix) {
			count++
		}
	}
	return count
}

func captureWatchdogLogs() (*[]string, func(string, ...any)) {
	logs := []string{}
	return &logs, func(format string, args ...any) {
		logs = append(logs, fmt.Sprintf(format, args...))
	}
}

func testWatchdogConfig() config {
	return config{
		BotSession:           "prixok-bot",
		BotLauncher:          "/tmp/prixok-bot.sh",
		LocalHealth:          "/tmp/local-health.sh",
		BrowserEnsure:        "/tmp/browser-ensure.sh",
		NetworkState:         "/tmp/network-state.sh",
		ProotDistro:          "debian",
		BotLockPath:          "/app/.atri-prixok-bot-v133.lock",
		LoopInterval:         30 * time.Second,
		CommandTimeout:       5 * time.Second,
		RepairTimeout:        270 * time.Second,
		NetworkCheckInterval: 180 * time.Second,
		NetworkProbeTimeout:  8 * time.Second,
	}
}

func TestWatchdogDoesNotDuplicateActiveWorker(t *testing.T) {
	config := testWatchdogConfig()
	checkKey := "tmux has-session -t prixok-bot"
	lockPrefix := "proot-distro login debian -- bash -lc"
	runner := &fakeWatchdogRunner{responses: map[string][]error{
		checkKey: {errors.New("missing tmux session")},
	}}
	logs, logf := captureWatchdogLogs()
	watchdog := newWatchdog(config, runner, func(path string) bool { return path == config.BotLauncher }, logf)

	watchdog.ensureBotSession(context.Background())
	if runner.countPrefix(lockPrefix) != 1 {
		t.Fatalf("lock checks=%d", runner.countPrefix(lockPrefix))
	}
	if runner.countPrefix("tmux new-session") != 0 {
		t.Fatal("worker lock was held but watchdog started a duplicate tmux session")
	}
	if len(*logs) != 1 || (*logs)[0] != "BOT_SESSION_MISSING_WORKER_ACTIVE" {
		t.Fatalf("logs=%v", *logs)
	}
}

func TestWatchdogRestartsMissingSessionWhenWorkerLockIsFree(t *testing.T) {
	config := testWatchdogConfig()
	checkKey := "tmux has-session -t prixok-bot"
	lockPrefix := "proot-distro login debian -- bash -lc"
	runner := &fakeWatchdogRunner{responses: map[string][]error{
		checkKey: {errors.New("missing tmux session")},
	}}
	logs, logf := captureWatchdogLogs()
	watchdog := newWatchdog(config, runner, func(path string) bool { return path == config.BotLauncher }, logf)

	// A non-zero lock probe means flock acquired the lock, so there is no active bot worker.
	runner.responses[fakeWatchdogCommandKey(watchdogCommand{
		Path: "proot-distro",
		Args: []string{"login", config.ProotDistro, "--", "bash", "-lc", `lock_path=$1; exec 9>>"$lock_path"; if flock -n 9; then flock -u 9; exit 1; fi; exit 0`, "watchdog-lock", config.BotLockPath},
	})] = []error{errors.New("lock is free")}

	watchdog.ensureBotSession(context.Background())
	if runner.countPrefix(lockPrefix) != 1 {
		t.Fatalf("lock checks=%d", runner.countPrefix(lockPrefix))
	}
	if runner.countPrefix("tmux new-session -d -s prixok-bot") != 1 {
		t.Fatalf("restart calls=%d", runner.countPrefix("tmux new-session -d -s prixok-bot"))
	}
	if len(*logs) != 1 || (*logs)[0] != "BOT_SESSION_RESTART" {
		t.Fatalf("logs=%v", *logs)
	}
}

func TestWatchdogRepairBackoffMatchesProductionLoop(t *testing.T) {
	config := testWatchdogConfig()
	healthKey := config.LocalHealth + " --quiet"
	repairKey := config.BrowserEnsure + " --from-watchdog"
	runner := &fakeWatchdogRunner{responses: map[string][]error{
		healthKey: {errors.New("unhealthy"), errors.New("unhealthy"), errors.New("unhealthy")},
		repairKey: {errors.New("repair failed"), errors.New("repair failed again")},
	}}
	watchdog := newWatchdog(config, runner, func(path string) bool {
		return path == config.LocalHealth
	}, nil)
	now := time.Unix(10_000, 0)

	if delay := watchdog.tick(context.Background(), now); delay != 30*time.Second {
		t.Fatalf("first delay=%s", delay)
	}
	if watchdog.repair.failures != 1 || watchdog.repair.nextAt != now.Add(30*time.Second) {
		t.Fatalf("backoff=%+v", watchdog.repair)
	}
	if repairCalls := runner.countPrefix(repairKey); repairCalls != 1 {
		t.Fatalf("repair calls=%d", repairCalls)
	}

	if delay := watchdog.tick(context.Background(), now.Add(10*time.Second)); delay != watchdogBackoffPollInterval {
		t.Fatalf("backoff poll delay=%s", delay)
	}
	if repairCalls := runner.countPrefix(repairKey); repairCalls != 1 {
		t.Fatalf("repair retried during backoff: %d", repairCalls)
	}

	if delay := watchdog.tick(context.Background(), now.Add(30*time.Second)); delay != 30*time.Second {
		t.Fatalf("deadline delay=%s", delay)
	}
	if watchdog.repair.failures != 2 || watchdog.repair.nextAt != now.Add(90*time.Second) {
		t.Fatalf("second backoff=%+v", watchdog.repair)
	}
}

func TestWatchdogNetworkLogsOnlyTransitions(t *testing.T) {
	config := testWatchdogConfig()
	networkKey := config.NetworkState + " --via-socks"
	runner := &fakeWatchdogRunner{responses: map[string][]error{
		networkKey: {nil, errors.New("offline"), errors.New("still offline")},
	}}
	logs, logf := captureWatchdogLogs()
	watchdog := newWatchdog(config, runner, func(path string) bool { return path == config.NetworkState }, logf)
	now := time.Unix(20_000, 0)

	watchdog.checkNetwork(context.Background(), now)
	watchdog.checkNetwork(context.Background(), now.Add(180*time.Second))
	watchdog.checkNetwork(context.Background(), now.Add(360*time.Second))
	if got := strings.Join(*logs, ","); got != "NETWORK_STATE=ONLINE,NETWORK_STATE=PENDING_NONBLOCKING" {
		t.Fatalf("logs=%v", *logs)
	}
}
