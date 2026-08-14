package runtimecfg

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
)

var ErrMCPBackendClosed = errors.New("MCP transport backend is closed")

// SupervisedMCPBackend gates the shared transport backend so shutdown cannot
// close sessions underneath active tool calls and no new calls can start after
// close begins. The same instance should be shared by the MCP runtime and the
// supervisor lifecycle.
type SupervisedMCPBackend struct {
	backend *MCPTransportBackend

	gate           sync.RWMutex
	closed         atomic.Bool
	shutdownCtx    context.Context
	shutdownCancel context.CancelFunc
}

var _ MCPBackend = (*SupervisedMCPBackend)(nil)

func NewSupervisedMCPBackend(backend *MCPTransportBackend) *SupervisedMCPBackend {
	shutdownCtx, shutdownCancel := context.WithCancel(context.Background())
	return &SupervisedMCPBackend{
		backend:        backend,
		shutdownCtx:    shutdownCtx,
		shutdownCancel: shutdownCancel,
	}
}

func (backend *SupervisedMCPBackend) begin(
	ctx context.Context,
) (context.Context, func(), error) {
	if backend == nil {
		return nil, nil, errors.New("supervised MCP backend is nil")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	backend.gate.RLock()
	if backend.closed.Load() {
		backend.gate.RUnlock()
		return nil, nil, ErrMCPBackendClosed
	}
	operationCtx, cancel := context.WithCancel(ctx)
	stopShutdownHook := context.AfterFunc(backend.shutdownCtx, cancel)
	cleanup := func() {
		stopShutdownHook()
		cancel()
		backend.gate.RUnlock()
	}
	return operationCtx, cleanup, nil
}

func (backend *SupervisedMCPBackend) ListTools(
	ctx context.Context,
	plugin string,
) ([]MCPTool, error) {
	operationCtx, cleanup, err := backend.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer cleanup()
	if backend.backend == nil {
		return nil, errors.New("MCP transport backend is not configured")
	}
	return backend.backend.ListTools(operationCtx, plugin)
}

func (backend *SupervisedMCPBackend) CallTool(
	ctx context.Context,
	plugin string,
	tool string,
	arguments map[string]any,
) (MCPCallResult, error) {
	operationCtx, cleanup, err := backend.begin(ctx)
	if err != nil {
		return MCPCallResult{}, err
	}
	defer cleanup()
	if backend.backend == nil {
		return MCPCallResult{}, errors.New("MCP transport backend is not configured")
	}
	return backend.backend.CallTool(operationCtx, plugin, tool, arguments)
}

func (backend *SupervisedMCPBackend) Prewarm(
	ctx context.Context,
	plugins []string,
	concurrency int,
) map[string]string {
	operationCtx, cleanup, err := backend.begin(ctx)
	if err != nil {
		return closedMCPPrewarmResults(plugins, err.Error())
	}
	defer cleanup()
	if backend.backend == nil {
		return closedMCPPrewarmResults(plugins, "MCP transport backend is not configured")
	}
	return backend.backend.Prewarm(operationCtx, plugins, concurrency)
}

func closedMCPPrewarmResults(plugins []string, message string) map[string]string {
	if len(plugins) == 0 {
		plugins = append([]string(nil), MCPPluginNames...)
	}
	results := map[string]string{}
	for _, raw := range plugins {
		plugin := normalizeMCPPlugin(raw)
		if plugin == "" {
			continue
		}
		results[plugin] = message
	}
	if len(results) == 0 {
		results["mcp"] = message
	}
	return results
}

func (backend *SupervisedMCPBackend) PruneIdle() int {
	if backend == nil {
		return 0
	}
	backend.gate.RLock()
	defer backend.gate.RUnlock()
	if backend.closed.Load() || backend.backend == nil {
		return 0
	}
	return backend.backend.PruneIdle()
}

func (backend *SupervisedMCPBackend) Close() error {
	if backend == nil {
		return nil
	}
	if !backend.closed.CompareAndSwap(false, true) {
		return nil
	}
	if backend.shutdownCancel != nil {
		backend.shutdownCancel()
	}

	backend.gate.Lock()
	defer backend.gate.Unlock()
	if backend.backend == nil {
		return nil
	}
	return backend.backend.Close()
}

func (backend *SupervisedMCPBackend) Closed() bool {
	return backend == nil || backend.closed.Load()
}
