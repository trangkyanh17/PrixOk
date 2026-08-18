package main

import (
	"context"
	"errors"
	"strconv"
	"time"
)

const watchdogBackoffPollInterval = 10 * time.Second

const botLockFreeExitCode = 10

type botLockState uint8

const (
	botLockUnknown botLockState = iota
	botLockFree
	botLockHeld
)

type watchdog struct {
	config     config
	runner     watchdogCommandRunner
	executable func(string) bool
	logf       func(string, ...any)
	now        func() time.Time

	repair                 repairBackoff
	lastNetworkCheck       time.Time
	lastNetworkState       string
	lastBackoffLog         time.Time
	observeLogged          bool
	botSessionMissingSince time.Time
	lastOrphanRecovery     time.Time
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
		now:        time.Now,
	}
}

func (watchdog *watchdog) currentTime() time.Time {
	if watchdog == nil || watchdog.now == nil {
		return time.Now()
	}
	return watchdog.now()
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

func (watchdog *watchdog) botLockProbeCommand() watchdogCommand {
	return watchdogCommand{
		Path: "proot-distro",
		Args: []string{
			"login",
			watchdog.config.ProotDistro,
			"--",
			"bash",
			"-lc",
			`lock_path=$1
if [ ! -e "$lock_path" ]; then exit 10; fi
command -v flock >/dev/null 2>&1 || exit 20
exec 9<>"$lock_path" || exit 21
if flock -n -E 11 9; then
  flock -u 9 || exit 22
  exit 10
else
  # Capture flock itself here; $? after fi would be the compound-if status.
  rc=$?
  [ "$rc" -eq 11 ] && exit 0
  exit 23
fi
`,
			"watchdog-lock",
			watchdog.config.BotLockPath,
		},
	}
}

func (watchdog *watchdog) botWorkerLockState(ctx context.Context) botLockState {
	command := watchdog.botLockProbeCommand()
	err := watchdog.runCommand(ctx, watchdog.config.CommandTimeout, command)
	if err == nil {
		return botLockHeld
	}
	if commandExitCode(err) == botLockFreeExitCode {
		return botLockFree
	}
	watchdog.log("BOT_LOCK_PROBE_UNKNOWN rc=%d", commandExitCode(err))
	return botLockUnknown
}

func (watchdog *watchdog) resetBotOrphanTracking() {
	watchdog.botSessionMissingSince = time.Time{}
	watchdog.lastOrphanRecovery = time.Time{}
}

func (watchdog *watchdog) launchBotSession(ctx context.Context) {
	if watchdog.config.WatchdogObserveOnly {
		watchdog.log("BOT_SESSION_MISSING_OBSERVE_ONLY")
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

func (watchdog *watchdog) recoverVerifiedOrphan(ctx context.Context, now time.Time) {
	if watchdog.config.WatchdogObserveOnly {
		return
	}
	if !watchdog.executable(watchdog.config.BotOrphanRecovery) {
		watchdog.log("BOT_ORPHAN_RECOVERY_MISSING")
		return
	}
	if !watchdog.lastOrphanRecovery.IsZero() && now.Sub(watchdog.lastOrphanRecovery) < watchdog.config.OrphanRetry {
		return
	}
	watchdog.lastOrphanRecovery = now
	watchdog.log("BOT_ORPHAN_RECOVERY_START")
	command := watchdogCommand{
		Path: watchdog.config.BotOrphanRecovery,
		Args: []string{"--orphan-recover"},
		Env: []string{
			"ATRI_BOT_SESSION=" + watchdog.config.BotSession,
			"ATRI_BOT_LOCK_PATH=" + watchdog.config.BotLockPath,
			"ATRI_PROOT_DISTRO=" + watchdog.config.ProotDistro,
		},
	}
	if err := watchdog.runCommand(ctx, watchdog.config.OrphanRecoveryTimeout, command); err != nil {
		if ctx.Err() == nil {
			watchdog.log("BOT_ORPHAN_RECOVERY_FAIL rc=%d", commandExitCode(err))
		}
		return
	}
	watchdog.log("BOT_ORPHAN_RECOVERY_RELEASED")
}

func (watchdog *watchdog) ensureBotSession(ctx context.Context) {
	check := watchdogCommand{
		Path: "tmux",
		Args: []string{"has-session", "-t", watchdog.config.BotSession},
	}
	if watchdog.runCommand(ctx, watchdog.config.CommandTimeout, check) == nil || ctx.Err() != nil {
		watchdog.resetBotOrphanTracking()
		return
	}

	switch watchdog.botWorkerLockState(ctx) {
	case botLockUnknown:
		watchdog.botSessionMissingSince = time.Time{}
		watchdog.log("BOT_SESSION_MISSING_LOCK_UNKNOWN")
		return
	case botLockFree:
		watchdog.resetBotOrphanTracking()
		watchdog.launchBotSession(ctx)
		return
	case botLockHeld:
		now := watchdog.currentTime()
		if watchdog.botSessionMissingSince.IsZero() {
			watchdog.botSessionMissingSince = now
		}
		elapsed := now.Sub(watchdog.botSessionMissingSince)
		watchdog.log("BOT_SESSION_MISSING_WORKER_ACTIVE elapsed=%s", elapsed.Round(time.Second))
		if elapsed < watchdog.config.OrphanGrace {
			return
		}
		watchdog.recoverVerifiedOrphan(ctx, now)
		if ctx.Err() != nil {
			return
		}
		if watchdog.botWorkerLockState(ctx) != botLockFree {
			return
		}
		watchdog.resetBotOrphanTracking()
		watchdog.launchBotSession(ctx)
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

func (watchdog *watchdog) repairSharedComponents(ctx context.Context) {
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
	delay := watchdog.repair.fail(watchdog.currentTime())
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

func (watchdog *watchdog) tick(ctx context.Context) time.Duration {
	delay := watchdog.normalizedLoopInterval()
	if watchdog.config.WatchdogObserveOnly && !watchdog.observeLogged {
		watchdog.log("WATCHDOG_OBSERVE_ONLY=ACTIVE")
		watchdog.observeLogged = true
	}

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
		if watchdog.config.WatchdogObserveOnly {
			watchdog.log("LOCAL_SHARED_COMPONENT_REPAIR=OBSERVE_ONLY")
		} else {
			now := watchdog.currentTime()
			if !watchdog.repair.ready(now) {
				if watchdog.shouldLogRepairBackoff(now) {
					watchdog.log("LOCAL_SHARED_COMPONENT_REPAIR=BACKOFF until=%d", watchdog.repair.nextAt.Unix())
				}
				return watchdogBackoffPollInterval
			}
			watchdog.repairSharedComponents(ctx)
		}
	}

	if ctx.Err() == nil {
		watchdog.checkNetwork(ctx, watchdog.currentTime())
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

		delay = watchdog.tick(ctx)
		if ctx.Err() != nil {
			return nil
		}
	}
}
