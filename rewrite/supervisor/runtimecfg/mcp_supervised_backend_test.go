package runtimecfg

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

type blockingMCPTransport struct {
	started      chan struct{}
	release      chan struct{}
	closeEntered chan struct{}
	startOnce    sync.Once
	closeOnce    sync.Once
}

func newBlockingMCPTransport() *blockingMCPTransport {
	return &blockingMCPTransport{
		started:      make(chan struct{}),
		release:      make(chan struct{}),
		closeEntered: make(chan struct{}),
	}
}

func (transport *blockingMCPTransport) Initialize(context.Context) error {
	return nil
}

func (transport *blockingMCPTransport) ListTools(context.Context) ([]MCPTool, error) {
	return []MCPTool{{Name: "blocking_tool"}}, nil
}

func (transport *blockingMCPTransport) CallTool(
	ctx context.Context,
	_ string,
	_ map[string]any,
) (MCPCallResult, error) {
	transport.startOnce.Do(func() { close(transport.started) })
	select {
	case <-transport.release:
		return MCPCallResult{Content: []any{"ok"}}, nil
	case <-ctx.Done():
		return MCPCallResult{}, ctx.Err()
	}
}

func (transport *blockingMCPTransport) Close() error {
	transport.closeOnce.Do(func() { close(transport.closeEntered) })
	return nil
}

func TestSupervisedMCPBackendCloseWaitsForActiveCall(t *testing.T) {
	transport := newBlockingMCPTransport()
	raw := NewMCPTransportBackend(nil)
	raw.Factory = func(MCPPluginSpec) (MCPTransport, error) {
		return transport, nil
	}
	backend := NewSupervisedMCPBackend(raw)

	if _, err := backend.ListTools(context.Background(), "context7"); err != nil {
		t.Fatal(err)
	}

	callDone := make(chan error, 1)
	go func() {
		_, err := backend.CallTool(context.Background(), "context7", "blocking_tool", nil)
		callDone <- err
	}()
	select {
	case <-transport.started:
	case <-time.After(2 * time.Second):
		t.Fatal("tool call did not start")
	}

	closeAttempted := make(chan struct{})
	closeDone := make(chan error, 1)
	go func() {
		close(closeAttempted)
		closeDone <- backend.Close()
	}()
	<-closeAttempted
	select {
	case <-transport.closeEntered:
		t.Fatal("transport close entered while tool call was active")
	case <-time.After(50 * time.Millisecond):
	}

	close(transport.release)
	if err := <-callDone; err != nil {
		t.Fatal(err)
	}
	select {
	case <-transport.closeEntered:
	case <-time.After(2 * time.Second):
		t.Fatal("transport close did not run after active call completed")
	}
	if err := <-closeDone; err != nil {
		t.Fatal(err)
	}
	if !backend.Closed() {
		t.Fatal("backend should be closed")
	}
	if _, err := backend.ListTools(context.Background(), "context7"); !errors.Is(err, ErrMCPBackendClosed) {
		t.Fatalf("post-close ListTools err=%v", err)
	}
	if _, err := backend.CallTool(context.Background(), "context7", "blocking_tool", nil); !errors.Is(err, ErrMCPBackendClosed) {
		t.Fatalf("post-close CallTool err=%v", err)
	}
	if err := backend.Close(); err != nil {
		t.Fatalf("idempotent close: %v", err)
	}
}

func TestSupervisedMCPBackendClosedPrewarmIsBounded(t *testing.T) {
	backend := NewSupervisedMCPBackend(NewMCPTransportBackend(nil))
	if err := backend.Close(); err != nil {
		t.Fatal(err)
	}
	results := backend.Prewarm(context.Background(), []string{"context7", "github"}, 2)
	if len(results) != 2 {
		t.Fatalf("results=%v", results)
	}
	for plugin, status := range results {
		if status != ErrMCPBackendClosed.Error() {
			t.Fatalf("%s=%q", plugin, status)
		}
	}
	if pruned := backend.PruneIdle(); pruned != 0 {
		t.Fatalf("pruned=%d", pruned)
	}
}
