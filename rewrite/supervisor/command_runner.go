package main

import (
	"context"
	"errors"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

const processGroupKillGrace = 2 * time.Second

type watchdogCommand struct {
	Path string
	Args []string
	Env  []string
}

type watchdogCommandRunner interface {
	Run(context.Context, watchdogCommand) error
}

type execWatchdogRunner struct{}

func (execWatchdogRunner) Run(ctx context.Context, command watchdogCommand) error {
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	cmd := exec.Command(command.Path, command.Args...)
	cmd.Env = append(os.Environ(), command.Env...)
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		return err
	}

	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
	}()

	select {
	case err := <-done:
		return err
	case <-ctx.Done():
	}

	_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
	timer := time.NewTimer(processGroupKillGrace)
	defer timer.Stop()
	select {
	case <-done:
		return ctx.Err()
	case <-timer.C:
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
		<-done
		return ctx.Err()
	}
}

func isExecutableFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir() && info.Mode()&0o111 != 0
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

func commandExitCode(err error) int {
	if err == nil {
		return 0
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return 124
	}
	if errors.Is(err, context.Canceled) {
		return 130
	}
	var exitErr *exec.ExitError
	if errors.As(err, &exitErr) {
		return exitErr.ExitCode()
	}
	if errors.Is(err, exec.ErrNotFound) || errors.Is(err, fs.ErrNotExist) {
		return 127
	}
	return 1
}
