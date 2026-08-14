package main

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestShellQuote(t *testing.T) {
	got := shellQuote("/tmp/Prix Ok/it's.sh")
	want := "'/tmp/Prix Ok/it'\"'\"'s.sh'"
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestCommandExitCode(t *testing.T) {
	if got := commandExitCode(nil); got != 0 {
		t.Fatalf("success=%d", got)
	}
	if got := commandExitCode(context.DeadlineExceeded); got != 124 {
		t.Fatalf("timeout=%d", got)
	}
	if got := commandExitCode(context.Canceled); got != 130 {
		t.Fatalf("cancel=%d", got)
	}
	if got := commandExitCode(exec.ErrNotFound); got != 127 {
		t.Fatalf("notfound=%d", got)
	}
	if got := commandExitCode(errors.New("boom")); got != 1 {
		t.Fatalf("generic=%d", got)
	}
}

func TestExecWatchdogRunnerStopsOnContextCancel(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	started := time.Now()
	err := (execWatchdogRunner{}).Run(ctx, watchdogCommand{Path: "sh", Args: []string{"-c", "sleep 30"}})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("err=%v", err)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatalf("cancellation took %s", elapsed)
	}
}

func TestExecWatchdogRunnerKillsTermIgnoringDescendant(t *testing.T) {
	pidFile := t.TempDir() + "/child.pid"
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		done <- (execWatchdogRunner{}).Run(ctx, watchdogCommand{
			Path: "sh",
			Args: []string{"-c", `trap 'exit 0' TERM; sh -c 'trap "" TERM; echo $$ > "$PID_FILE"; while :; do sleep 30; done' & wait`},
			Env:  []string{"PID_FILE=" + pidFile},
		})
	}()

	var childPID int
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		data, err := os.ReadFile(pidFile)
		if err == nil {
			childPID, err = strconv.Atoi(strings.TrimSpace(string(data)))
			if err == nil && childPID > 0 {
				break
			}
		}
		time.Sleep(10 * time.Millisecond)
	}
	if childPID <= 0 {
		cancel()
		<-done
		t.Fatal("descendant did not publish its PID")
	}

	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("runner err=%v", err)
	}

	deadline = time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		err := syscall.Kill(childPID, 0)
		if errors.Is(err, syscall.ESRCH) {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("TERM-ignoring descendant %d survived runner cancellation", childPID)
}

func TestExecWatchdogRunnerCommandEnvOverridesParent(t *testing.T) {
	t.Setenv("ATRI_TEST_OVERRIDE", "old")
	err := (execWatchdogRunner{}).Run(context.Background(), watchdogCommand{
		Path: "sh",
		Args: []string{"-c", `[ "$ATRI_TEST_OVERRIDE" = "new" ]`},
		Env:  []string{"ATRI_TEST_OVERRIDE=new"},
	})
	if err != nil {
		t.Fatalf("override failed: %v", err)
	}
}
