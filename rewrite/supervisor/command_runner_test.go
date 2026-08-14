package main

import (
	"context"
	"errors"
	"os/exec"
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
