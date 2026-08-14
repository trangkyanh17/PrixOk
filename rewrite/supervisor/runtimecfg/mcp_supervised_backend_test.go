package runtimecfg

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type blockingMCPTransport struct {
	started      chan struct{}
	closeEntered chan struct{}
	startOnce    sync.Once
	closeOnce    sync.Once
}

func newBlockingMCPTransport() *blockingMCPTransport {
	return &blockingMCPTransport{
		started:      make(chan struct{}),
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
	<-ctx.Done()
	return MCPCallResult{}, ctx.Err()
}

func (transport *blockingMCPTransport) Close() error {
	transport.closeOnce.Do(func() { close(transport.closeEntered) })
	return nil
}

func TestSupervisedMCPBackendCloseCancelsActiveCall(t *testing.T) {
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

	closeDone := make(chan error, 1)
	go func() {
		closeDone <- backend.Close()
	}()

	select {
	case err := <-callDone:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("active call err=%v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("active tool call was not canceled by shutdown")
	}
	select {
	case err := <-closeDone:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("supervised backend close did not finish")
	}
	select {
	case <-transport.closeEntered:
	default:
		t.Fatal("transport close was not attempted")
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

func TestSupervisedMCPBackendPruneWaitsForActiveCall(t *testing.T) {
	transport := newBlockingMCPTransport()
	var clock atomic.Int64
	clock.Store(100)
	raw := NewMCPTransportBackend(nil)
	raw.IdleTTL = 50 * time.Second
	raw.Now = func() time.Time { return time.Unix(clock.Load(), 0) }
	raw.Factory = func(MCPPluginSpec) (MCPTransport, error) { return transport, nil }
	backend := NewSupervisedMCPBackend(raw)

	if _, err := backend.ListTools(context.Background(), "context7"); err != nil {
		t.Fatal(err)
	}
	callCtx, cancelCall := context.WithCancel(context.Background())
	callDone := make(chan error, 1)
	go func() {
		_, err := backend.CallTool(callCtx, "context7", "blocking_tool", nil)
		callDone <- err
	}()
	select {
	case <-transport.started:
	case <-time.After(2 * time.Second):
		t.Fatal("tool call did not start")
	}

	clock.Store(200)
	pruneDone := make(chan int, 1)
	go func() { pruneDone <- backend.PruneIdle() }()
	select {
	case count := <-pruneDone:
		t.Fatalf("prune ran during active call: %d", count)
	case <-time.After(50 * time.Millisecond):
	}

	cancelCall()
	select {
	case err := <-callDone:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("call err=%v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("call did not stop")
	}
	select {
	case count := <-pruneDone:
		if count != 1 {
			t.Fatalf("pruned=%d want=1", count)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("prune did not resume")
	}
	select {
	case <-transport.closeEntered:
	default:
		t.Fatal("prune did not close stale transport")
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

func TestSupervisedMCPBackendNilAndUnconfigured(t *testing.T) {
	var nilBackend *SupervisedMCPBackend
	if _, err := nilBackend.ListTools(context.Background(), "context7"); err == nil {
		t.Fatal("nil supervised backend should fail")
	}

	backend := NewSupervisedMCPBackend(nil)
	if _, err := backend.ListTools(context.Background(), "context7"); err == nil {
		t.Fatal("unconfigured transport backend should fail")
	}
	if err := backend.Close(); err != nil {
		t.Fatal(err)
	}
}
