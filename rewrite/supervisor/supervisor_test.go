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
