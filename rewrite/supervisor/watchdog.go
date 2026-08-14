package main

import (
	"context"
	"errors"
	"strconv"
	"time"
)

const watchdogBackoffPollInterval = 10 * time.Second

type watchdog struct {
	config     config
	runner     watchdogCommandRunner
	executable func(string) bool
	logf       func(string, ...any)

	repair           repairBackoff
	lastNetworkCheck time.Time
	lastNetworkState string
	lastBackoffLog   time.Time
}

func newWatchdog(
	config config,
	runner watchdogCommandRunner,
	executable func(string) bool,
	logf func(string, ...any),
) *watchdog {
	if executable == nil {
		executable = isExecutableFile
	}
	return &watchdog{
		config:     config,
		runner:     runner,
		executable: executable,
		logf:       logf,
	}
}

func (watchdog *watchdog) normalizedLoopInterval() time.Duration {
	if watchdog == nil || watchdog.config.LoopInterval <= 0 {
		return 30 * time.Second
	}
	return watchdog.config.LoopInterval
}

func (watchdog *watchdog) runCommand(
	ctx context.Context,
	timeout time.Duration,
	command watchdogCommand,
) error {
	if ctx == nil {
		ctx = context.Background()
	}
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	commandCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	return watchdog.runner.Run(commandCtx, command)
}

func (watchdog *watchdog) log(format string, args ...any) {
	if watchdog != nil && watchdog.logf != nil {
		watchdog.logf(format, args...)
	}
}

func (watchdog *watchdog) botWorkerLockHeld(ctx context.Context) bool {
	command := watchdogCommand{
		Path: "proot-distro",
		Args: []string{
			"login",
			watchdog.config.ProotDistro,
			"--",
			"bash",
			"-lc",
			`lock_path=$1; exec 9>>"$lock_path"; if flock -n 9; then flock -u 9; exit 1; fi; exit 0`,
			"watchdog-lock",
			watchdog.config.BotLockPath,
		},
	}
	return watchdog.runCommand(ctx, watchdog.config.CommandTimeout, command) == nil
}

func (watchdog *watchdog) ensureBotSession(ctx context.Context) {
	check := watchdogCommand{
		Path: "tmux",
		Args: []string{"has-session", "-t", watchdog.config.BotSession},
	}
	if watchdog.runCommand(ctx, watchdog.config.CommandTimeout, check) == nil || ctx.Err() != nil {
		return
	}
	if watchdog.botWorkerLockHeld(ctx) {
		watchdog.log("BOT_SESSION_MISSING_WORKER_ACTIVE")
		return
	}
	if !watchdog.executable(watchdog.config.BotLauncher) {
		watchdog.log("BOT_LAUNCHER_MISSING")
		return
	}

	watchdog.log("BOT_SESSION_RESTART")
	launch := watchdogCommand{
		Path: "tmux",
		Args: []string{
			"new-session",
			"-d",
			"-s",
			watchdog.config.BotSession,
			"exec bash " + shellQuote(watchdog.config.BotLauncher),
		},
	}
	if err := watchdog.runCommand(ctx, watchdog.config.CommandTimeout, launch); err != nil && ctx.Err() == nil {
		watchdog.log("BOT_SESSION_RESTART_FAIL")
	}
}

func (watchdog *watchdog) localHealthOK(ctx context.Context) bool {
	if !watchdog.executable(watchdog.config.LocalHealth) {
		return false
	}
	command := watchdogCommand{
		Path: watchdog.config.LocalHealth,
		Args: []string{"--quiet"},
	}
	return watchdog.runCommand(ctx, watchdog.config.CommandTimeout, command) == nil
}

func (watchdog *watchdog) repairSharedComponents(ctx context.Context, now time.Time) {
	command := watchdogCommand{
		Path: watchdog.config.BrowserEnsure,
		Args: []string{"--from-watchdog"},
	}
	err := watchdog.runCommand(ctx, watchdog.config.RepairTimeout, command)
	if ctx.Err() != nil {
		return
	}
	if err == nil {
		watchdog.log("LOCAL_SHARED_COMPONENT_REPAIR=PASS")
		watchdog.repair.reset()
		return
	}

	watchdog.log("LOCAL_SHARED_COMPONENT_REPAIR=FAIL rc=%d", commandExitCode(err))
	delay := watchdog.repair.fail(now)
	watchdog.log("LOCAL_SHARED_COMPONENT_REPAIR_BACKOFF=%s", delay)
}

func (watchdog *watchdog) shouldLogRepairBackoff(now time.Time) bool {
	if watchdog.lastBackoffLog.IsZero() || now.Sub(watchdog.lastBackoffLog) >= time.Minute {
		watchdog.lastBackoffLog = now
		return true
	}
	return false
}

func (watchdog *watchdog) networkCheckDue(now time.Time) bool {
	interval := watchdog.config.NetworkCheckInterval
	if interval <= 0 {
		interval = 180 * time.Second
	}
	return watchdog.lastNetworkCheck.IsZero() || now.Sub(watchdog.lastNetworkCheck) >= interval
}

func (watchdog *watchdog) checkNetwork(ctx context.Context, now time.Time) {
	if !watchdog.networkCheckDue(now) {
		return
	}
	watchdog.lastNetworkCheck = now
	if !watchdog.executable(watchdog.config.NetworkState) {
		return
	}

	timeout := watchdog.config.NetworkProbeTimeout
	if timeout <= 0 {
		timeout = 8 * time.Second
	}
	seconds := int(timeout / time.Second)
	if seconds < 1 {
		seconds = 1
	}
	command := watchdogCommand{
		Path: watchdog.config.NetworkState,
		Args: []string{"--via-socks"},
		Env:  []string{"ATRI_NETWORK_PROBE_TIMEOUT=" + strconv.Itoa(seconds)},
	}
	state := "PENDING_NONBLOCKING"
	if watchdog.runCommand(ctx, timeout, command) == nil {
		state = "ONLINE"
	}
	if ctx.Err() != nil || state == watchdog.lastNetworkState {
		return
	}
	watchdog.log("NETWORK_STATE=%s", state)
	watchdog.lastNetworkState = state
}

func (watchdog *watchdog) tick(ctx context.Context, now time.Time) time.Duration {
	delay := watchdog.normalizedLoopInterval()
	watchdog.ensureBotSession(ctx)
	if ctx.Err() != nil {
		return delay
	}

	if watchdog.localHealthOK(ctx) {
		watchdog.repair.reset()
	} else {
		if ctx.Err() != nil {
			return delay
		}
		watchdog.log("LOCAL_SHARED_COMPONENT_HEALTH=UNHEALTHY")
		if !watchdog.repair.ready(now) {
			if watchdog.shouldLogRepairBackoff(now) {
				watchdog.log("LOCAL_SHARED_COMPONENT_REPAIR=BACKOFF until=%d", watchdog.repair.nextAt.Unix())
			}
			return watchdogBackoffPollInterval
		}
		watchdog.repairSharedComponents(ctx, now)
	}

	if ctx.Err() == nil {
		watchdog.checkNetwork(ctx, now)
	}
	return delay
}

func (watchdog *watchdog) Run(ctx context.Context) error {
	if watchdog == nil || watchdog.runner == nil {
		return errors.New("watchdog runner is not configured")
	}
	if ctx == nil {
		ctx = context.Background()
	}

	delay := time.Duration(0)
	for {
		if delay > 0 {
			timer := time.NewTimer(delay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return nil
			case <-timer.C:
			}
		}

		delay = watchdog.tick(ctx, time.Now())
		if ctx.Err() != nil {
			return nil
		}
	}
}
