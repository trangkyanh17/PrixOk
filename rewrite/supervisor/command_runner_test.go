package main

import (
	"context"
	"errors"
	"os/exec"
	"testing"
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
