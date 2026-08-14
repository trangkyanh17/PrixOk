package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestSupervisorParentShutdownIsClean(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	started := make(chan struct{}, 2)
	component := func(ctx context.Context) error {
		started <- struct{}{}
		<-ctx.Done()
		return ctx.Err()
	}
	done := make(chan error, 1)
	go func() {
		done <- runSupervisorComponents(ctx, []supervisorComponent{
			{Name: "watchdog", Run: component},
			{Name: "mcp", Run: component},
		})
	}()
	<-started
	<-started
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("shutdown err=%v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("supervisor did not stop")
	}
}

func TestSupervisorFailureCancelsPeers(t *testing.T) {
	peerCanceled := make(chan struct{})
	err := runSupervisorComponents(context.Background(), []supervisorComponent{
		{Name: "watchdog", Run: func(context.Context) error { return errors.New("watchdog failed") }},
		{Name: "mcp", Run: func(ctx context.Context) error {
			<-ctx.Done()
			close(peerCanceled)
			return ctx.Err()
		}},
	})
	if err == nil || !strings.Contains(err.Error(), "watchdog: watchdog failed") {
		t.Fatalf("err=%v", err)
	}
	select {
	case <-peerCanceled:
	default:
		t.Fatal("peer component was not canceled")
	}
}

func TestSupervisorTreatsEarlyNilExitAsFailure(t *testing.T) {
	peerCanceled := make(chan struct{})
	err := runSupervisorComponents(context.Background(), []supervisorComponent{
		{Name: "watchdog", Run: func(context.Context) error { return nil }},
		{Name: "mcp", Run: func(ctx context.Context) error {
			<-ctx.Done()
			close(peerCanceled)
			return ctx.Err()
		}},
	})
	if err == nil || !strings.Contains(err.Error(), "watchdog stopped unexpectedly") {
		t.Fatalf("err=%v", err)
	}
	select {
	case <-peerCanceled:
	default:
		t.Fatal("peer component was not canceled")
	}
}

func TestSupervisorShutdownTimeoutBoundsHungComponent(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	started := make(chan struct{})
	release := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		done <- runSupervisorComponentsWithTimeout(ctx, []supervisorComponent{
			{Name: "stuck", Run: func(context.Context) error {
				close(started)
				<-release
				return nil
			}},
		}, 25*time.Millisecond)
	}()

	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("stuck component did not start")
	}
	cancel()

	select {
	case err := <-done:
		if err == nil || !strings.Contains(err.Error(), "supervisor shutdown timed out after 25ms waiting for stuck") {
			t.Fatalf("err=%v", err)
		}
		close(release)
	case <-time.After(time.Second):
		close(release)
		t.Fatal("supervisor shutdown timeout was not enforced")
	}
}

func TestSupervisorFailureTimeoutPreservesOriginalError(t *testing.T) {
	release := make(chan struct{})
	err := runSupervisorComponentsWithTimeout(context.Background(), []supervisorComponent{
		{Name: "watchdog", Run: func(context.Context) error { return errors.New("boom") }},
		{Name: "mcp", Run: func(context.Context) error {
			<-release
			return nil
		}},
	}, 25*time.Millisecond)
	close(release)
	if err == nil || !strings.Contains(err.Error(), "watchdog: boom") ||
		!strings.Contains(err.Error(), "supervisor shutdown timed out") ||
		!strings.Contains(err.Error(), "mcp") {
		t.Fatalf("err=%v", err)
	}
}

func TestRunRewriteSupervisorNoComponentsReturnsCleanly(t *testing.T) {
	t.Setenv("ATRI_REWRITE_WATCHDOG", "false")
	t.Setenv("ATRI_REWRITE_MCP_LIFECYCLE", "false")
	if err := runRewriteSupervisor(); err != nil {
		t.Fatalf("err=%v", err)
	}
}
