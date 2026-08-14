package main

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

type mcpPrewarmCall struct {
	plugins     []string
	concurrency int
	hasDeadline bool
}

type fakeMCPLifecycleBackend struct {
	prewarmCalls chan mcpPrewarmCall
	pruneCalls   chan struct{}
	closeCalls   chan struct{}
	pruned       int
	closeErr     error
}

func newFakeMCPLifecycleBackend() *fakeMCPLifecycleBackend {
	return &fakeMCPLifecycleBackend{
		prewarmCalls: make(chan mcpPrewarmCall, 8),
		pruneCalls:   make(chan struct{}, 8),
		closeCalls:   make(chan struct{}, 8),
	}
}

func (backend *fakeMCPLifecycleBackend) Prewarm(
	ctx context.Context,
	plugins []string,
	concurrency int,
) map[string]string {
	_, hasDeadline := ctx.Deadline()
	backend.prewarmCalls <- mcpPrewarmCall{
		plugins:     append([]string(nil), plugins...),
		concurrency: concurrency,
		hasDeadline: hasDeadline,
	}
	result := map[string]string{}
	for _, plugin := range plugins {
		result[plugin] = "ready:1"
	}
	return result
}

func (backend *fakeMCPLifecycleBackend) PruneIdle() int {
	backend.pruneCalls <- struct{}{}
	return backend.pruned
}

func (backend *fakeMCPLifecycleBackend) Close() error {
	backend.closeCalls <- struct{}{}
	return backend.closeErr
}

func receiveWithin[T any](t *testing.T, channel <-chan T) T {
	t.Helper()
	select {
	case value := <-channel:
		return value
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for lifecycle event")
		var zero T
		return zero
	}
}

func TestMCPLifecyclePrewarmRefreshPruneAndClose(t *testing.T) {
	backend := newFakeMCPLifecycleBackend()
	backend.pruned = 2
	logs := []string{}
	lifecycle := newMCPLifecycle(backend, mcpLifecycleConfig{
		Plugins:     []string{"serena", "context7"},
		Concurrency: 2,
		Timeout:     time.Second,
	}, func(format string, args ...any) {
		logs = append(logs, format)
	})

	ctx, cancel := context.WithCancel(context.Background())
	health := make(chan time.Time, 1)
	prune := make(chan time.Time, 1)
	done := make(chan error, 1)
	go func() {
		done <- lifecycle.runWithTicks(ctx, health, prune)
	}()

	startup := receiveWithin(t, backend.prewarmCalls)
	if !reflect.DeepEqual(startup.plugins, []string{"serena", "context7"}) || startup.concurrency != 2 {
		t.Fatalf("startup=%+v", startup)
	}
	if !startup.hasDeadline {
		t.Fatal("startup prewarm did not receive a deadline")
	}

	health <- time.Now()
	refresh := receiveWithin(t, backend.prewarmCalls)
	if !reflect.DeepEqual(refresh.plugins, startup.plugins) {
		t.Fatalf("refresh plugins=%v startup=%v", refresh.plugins, startup.plugins)
	}

	prune <- time.Now()
	receiveWithin(t, backend.pruneCalls)
	cancel()
	if err := receiveWithin(t, done); err != nil {
		t.Fatal(err)
	}
	receiveWithin(t, backend.closeCalls)
	if len(logs) < 3 {
		t.Fatalf("expected startup, health and prune logs; got %d", len(logs))
	}
}

func TestMCPLifecycleReturnsCloseError(t *testing.T) {
	backend := newFakeMCPLifecycleBackend()
	backend.closeErr = errors.New("close failed")
	lifecycle := newMCPLifecycle(backend, mcpLifecycleConfig{Timeout: time.Second}, nil)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := lifecycle.runWithTicks(ctx, nil, nil); err == nil || !strings.Contains(err.Error(), "close failed") {
		t.Fatalf("err=%v", err)
	}
}

func TestMCPLifecycleRejectsMissingBackend(t *testing.T) {
	lifecycle := newMCPLifecycle(nil, mcpLifecycleConfig{}, nil)
	if err := lifecycle.Run(context.Background()); err == nil {
		t.Fatal("expected missing backend error")
	}
}

func TestFormatMCPResultsIsDeterministicSingleLineAndControlSafe(t *testing.T) {
	long := strings.Repeat("đ", 300)
	result := formatMCPResults(map[string]string{
		"serena\nplugin": "failed\nwith\tnewlines\x1b[31m",
		"context7":       long,
	})
	if strings.ContainsAny(result, "\r\n\t\x1b") {
		t.Fatalf("result contains control characters: %q", result)
	}
	if !strings.HasPrefix(result, "context7=") || !strings.Contains(result, " serena plugin=failed with newlines [31m") {
		t.Fatalf("result=%q", result)
	}
	if !strings.Contains(result, "...") {
		t.Fatalf("long status was not truncated: %q", result)
	}
}
