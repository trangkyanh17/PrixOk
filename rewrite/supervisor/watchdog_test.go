package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

func fakeWatchdogCommandKey(command watchdogCommand) string {
	return strings.TrimSpace(fmt.Sprintf("%s %s", command.Path, strings.Join(command.Args, " ")))
}

type watchdogRunnerFunc func(context.Context, watchdogCommand) error

func (run watchdogRunnerFunc) Run(ctx context.Context, command watchdogCommand) error {
	return run(ctx, command)
}

type fakeExitError int

func (err fakeExitError) Error() string { return fmt.Sprintf("exit status %d", err) }
func (err fakeExitError) ExitCode() int { return int(err) }

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
		BotSession:            "prixok-bot",
		BotLauncher:           "/tmp/prixok-bot.sh",
		BotOrphanRecovery:     "/tmp/termux-atri-final-recovery.sh",
		LocalHealth:           "/tmp/local-health.sh",
		BrowserEnsure:         "/tmp/browser-ensure.sh",
		NetworkState:          "/tmp/network-state.sh",
		ProotDistro:           "debian",
		BotLockPath:           "/app/.atri-prixok-bot-v133.lock",
		LoopInterval:          30 * time.Second,
		CommandTimeout:        5 * time.Second,
		OrphanGrace:           90 * time.Second,
		OrphanRetry:           5 * time.Minute,
		OrphanRecoveryTimeout: 60 * time.Second,
		RepairTimeout:         270 * time.Second,
		NetworkCheckInterval:  180 * time.Second,
		NetworkProbeTimeout:   8 * time.Second,
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
	if len(*logs) != 1 || (*logs)[0] != "BOT_SESSION_MISSING_WORKER_ACTIVE elapsed=0s" {
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

	// Exit code 10 is the explicit, unambiguous lock-free result.
	runner.responses[fakeWatchdogCommandKey(watchdog.botLockProbeCommand())] = []error{fakeExitError(botLockFreeExitCode)}

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

func TestWatchdogLockProbeFailureFailsClosed(t *testing.T) {
	config := testWatchdogConfig()
	runner := &fakeWatchdogRunner{responses: map[string][]error{
		"tmux has-session -t prixok-bot": {errors.New("missing tmux session")},
	}}
	logs, logf := captureWatchdogLogs()
	watchdog := newWatchdog(config, runner, func(string) bool { return true }, logf)
	runner.responses[fakeWatchdogCommandKey(watchdog.botLockProbeCommand())] = []error{fakeExitError(20)}

	watchdog.ensureBotSession(context.Background())

	if runner.countPrefix("tmux new-session") != 0 {
		t.Fatal("unknown lock state created a duplicate bot session")
	}
	joined := strings.Join(*logs, ",")
	if !strings.Contains(joined, "BOT_LOCK_PROBE_UNKNOWN rc=20") ||
		!strings.Contains(joined, "BOT_SESSION_MISSING_LOCK_UNKNOWN") {
		t.Fatalf("logs=%v", *logs)
	}
}

func TestWatchdogLockProbeDistinguishesHeldAndFreeRealFlock(t *testing.T) {
	lockPath := t.TempDir() + "/production.lock"
	lockFile, err := os.OpenFile(lockPath, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer lockFile.Close()
	if err := syscall.Flock(int(lockFile.Fd()), syscall.LOCK_EX); err != nil {
		t.Fatal(err)
	}

	config := testWatchdogConfig()
	config.BotLockPath = lockPath
	watchdog := newWatchdog(config, &fakeWatchdogRunner{}, nil, nil)
	probe := watchdog.botLockProbeCommand()
	if len(probe.Args) != 8 || probe.Args[3] != "bash" || probe.Args[4] != "-lc" {
		t.Fatalf("unexpected probe command: %+v", probe)
	}
	runProbe := func() error {
		return exec.Command(probe.Args[3], probe.Args[4:]...).Run()
	}

	if err := runProbe(); err != nil {
		t.Fatalf("held lock was not reported as held: %v", err)
	}
	if err := syscall.Flock(int(lockFile.Fd()), syscall.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	err = runProbe()
	exitError, ok := err.(*exec.ExitError)
	if !ok || exitError.ExitCode() != botLockFreeExitCode {
		t.Fatalf("free lock exit=%v want=%d", err, botLockFreeExitCode)
	}
}

func TestWatchdogUnknownLockStateResetsOrphanGrace(t *testing.T) {
	config := testWatchdogConfig()
	runner := &fakeWatchdogRunner{responses: map[string][]error{
		"tmux has-session -t prixok-bot": {
			errors.New("missing"),
			errors.New("missing"),
			errors.New("missing"),
		},
	}}
	watchdog := newWatchdog(config, runner, func(string) bool { return true }, nil)
	runner.responses[fakeWatchdogCommandKey(watchdog.botLockProbeCommand())] = []error{
		nil,
		fakeExitError(20),
		nil,
	}
	now := time.Unix(45_000, 0)
	watchdog.now = func() time.Time { return now }

	watchdog.ensureBotSession(context.Background())
	now = now.Add(config.OrphanGrace)
	watchdog.ensureBotSession(context.Background())
	if !watchdog.botSessionMissingSince.IsZero() {
		t.Fatal("unknown lock state retained stale orphan grace evidence")
	}
	now = now.Add(config.OrphanGrace)
	watchdog.ensureBotSession(context.Background())
	if runner.countPrefix(config.BotOrphanRecovery+" --orphan-recover") != 0 {
		t.Fatal("orphan recovery ran without a fresh continuous grace period")
	}
}

func TestWatchdogRecoversVerifiedOrphanThenRestarts(t *testing.T) {
	config := testWatchdogConfig()
	checkKey := "tmux has-session -t prixok-bot"
	runner := &fakeWatchdogRunner{responses: map[string][]error{
		checkKey: {errors.New("missing"), errors.New("still missing")},
	}}
	logs, logf := captureWatchdogLogs()
	watchdog := newWatchdog(config, runner, func(path string) bool {
		return path == config.BotLauncher || path == config.BotOrphanRecovery
	}, logf)
	lockKey := fakeWatchdogCommandKey(watchdog.botLockProbeCommand())
	runner.responses[lockKey] = []error{nil, nil, fakeExitError(botLockFreeExitCode)}
	now := time.Unix(40_000, 0)
	watchdog.now = func() time.Time { return now }

	watchdog.ensureBotSession(context.Background())
	if runner.countPrefix(config.BotOrphanRecovery+" --orphan-recover") != 0 {
		t.Fatal("orphan recovery ran before grace period")
	}

	now = now.Add(config.OrphanGrace)
	watchdog.ensureBotSession(context.Background())
	if runner.countPrefix(config.BotOrphanRecovery+" --orphan-recover") != 1 {
		t.Fatal("verified orphan helper was not invoked exactly once")
	}
	if runner.countPrefix("tmux new-session -d -s prixok-bot") != 1 {
		t.Fatal("bot session was not recreated after verified lock release")
	}
	joined := strings.Join(*logs, ",")
	for _, marker := range []string{
		"BOT_ORPHAN_RECOVERY_START",
		"BOT_ORPHAN_RECOVERY_RELEASED",
		"BOT_SESSION_RESTART",
	} {
		if !strings.Contains(joined, marker) {
			t.Fatalf("missing %q in logs=%v", marker, *logs)
		}
	}
}

func TestWatchdogOrphanRecoveryFailureNeverStartsDuplicate(t *testing.T) {
	config := testWatchdogConfig()
	runner := &fakeWatchdogRunner{responses: map[string][]error{
		"tmux has-session -t prixok-bot":               {errors.New("missing")},
		config.BotOrphanRecovery + " --orphan-recover": {fakeExitError(75)},
	}}
	watchdog := newWatchdog(config, runner, func(path string) bool {
		return path == config.BotLauncher || path == config.BotOrphanRecovery
	}, nil)
	runner.responses[fakeWatchdogCommandKey(watchdog.botLockProbeCommand())] = []error{nil, nil}
	now := time.Unix(50_000, 0)
	watchdog.now = func() time.Time { return now }
	watchdog.botSessionMissingSince = now.Add(-config.OrphanGrace)

	watchdog.ensureBotSession(context.Background())
	if runner.countPrefix("tmux new-session") != 0 {
		t.Fatal("failed orphan recovery started a duplicate session")
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
	watchdog.now = func() time.Time { return now }

	if delay := watchdog.tick(context.Background()); delay != 30*time.Second {
		t.Fatalf("first delay=%s", delay)
	}
	if watchdog.repair.failures != 1 || watchdog.repair.nextAt != now.Add(30*time.Second) {
		t.Fatalf("backoff=%+v", watchdog.repair)
	}
	if repairCalls := runner.countPrefix(repairKey); repairCalls != 1 {
		t.Fatalf("repair calls=%d", repairCalls)
	}

	now = now.Add(10 * time.Second)
	if delay := watchdog.tick(context.Background()); delay != watchdogBackoffPollInterval {
		t.Fatalf("backoff poll delay=%s", delay)
	}
	if repairCalls := runner.countPrefix(repairKey); repairCalls != 1 {
		t.Fatalf("repair retried during backoff: %d", repairCalls)
	}

	now = now.Add(20 * time.Second)
	if delay := watchdog.tick(context.Background()); delay != 30*time.Second {
		t.Fatalf("deadline delay=%s", delay)
	}
	if watchdog.repair.failures != 2 || watchdog.repair.nextAt != now.Add(60*time.Second) {
		t.Fatalf("second backoff=%+v", watchdog.repair)
	}
}

func TestWatchdogRepairBackoffStartsAfterRepairCompletes(t *testing.T) {
	config := testWatchdogConfig()
	started := time.Unix(30_000, 0)
	now := started
	runner := watchdogRunnerFunc(func(_ context.Context, command watchdogCommand) error {
		switch fakeWatchdogCommandKey(command) {
		case "tmux has-session -t prixok-bot":
			return nil
		case config.LocalHealth + " --quiet":
			return errors.New("unhealthy")
		case config.BrowserEnsure + " --from-watchdog":
			now = now.Add(4 * time.Minute)
			return errors.New("repair timed out")
		default:
			return nil
		}
	})
	watchdog := newWatchdog(config, runner, func(path string) bool {
		return path == config.LocalHealth
	}, nil)
	watchdog.now = func() time.Time { return now }

	watchdog.tick(context.Background())
	want := started.Add(4*time.Minute + 30*time.Second)
	if watchdog.repair.nextAt != want {
		t.Fatalf("next repair=%s want=%s", watchdog.repair.nextAt, want)
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
