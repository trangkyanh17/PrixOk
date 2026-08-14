package runtimecfg

import (
	"context"
	"errors"
	"sync"
)

var ErrMCPBackendClosed = errors.New("MCP transport backend is closed")

// SupervisedMCPBackend gates the shared transport backend so shutdown cannot
// close sessions underneath active tool calls and no new calls can start after
// close begins. The same instance should be shared by the MCP runtime and the
// supervisor lifecycle.
type SupervisedMCPBackend struct {
	backend *MCPTransportBackend

	gate   sync.RWMutex
	closed bool
}

var _ MCPBackend = (*SupervisedMCPBackend)(nil)

func NewSupervisedMCPBackend(backend *MCPTransportBackend) *SupervisedMCPBackend {
	return &SupervisedMCPBackend{backend: backend}
}

func (backend *SupervisedMCPBackend) ListTools(
	ctx context.Context,
	plugin string,
) ([]MCPTool, error) {
	if backend == nil {
		return nil, errors.New("supervised MCP backend is nil")
	}
	backend.gate.RLock()
	defer backend.gate.RUnlock()
	if backend.closed {
		return nil, ErrMCPBackendClosed
	}
	if backend.backend == nil {
		return nil, errors.New("MCP transport backend is not configured")
	}
	return backend.backend.ListTools(ctx, plugin)
}

func (backend *SupervisedMCPBackend) CallTool(
	ctx context.Context,
	plugin string,
	tool string,
	arguments map[string]any,
) (MCPCallResult, error) {
	if backend == nil {
		return MCPCallResult{}, errors.New("supervised MCP backend is nil")
	}
	backend.gate.RLock()
	defer backend.gate.RUnlock()
	if backend.closed {
		return MCPCallResult{}, ErrMCPBackendClosed
	}
	if backend.backend == nil {
		return MCPCallResult{}, errors.New("MCP transport backend is not configured")
	}
	return backend.backend.CallTool(ctx, plugin, tool, arguments)
}

func (backend *SupervisedMCPBackend) Prewarm(
	ctx context.Context,
	plugins []string,
	concurrency int,
) map[string]string {
	if backend == nil {
		return closedMCPPrewarmResults(plugins, "supervised MCP backend is nil")
	}
	backend.gate.RLock()
	defer backend.gate.RUnlock()
	if backend.closed {
		return closedMCPPrewarmResults(plugins, ErrMCPBackendClosed.Error())
	}
	if backend.backend == nil {
		return closedMCPPrewarmResults(plugins, "MCP transport backend is not configured")
	}
	return backend.backend.Prewarm(ctx, plugins, concurrency)
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
	if backend.closed || backend.backend == nil {
		return 0
	}
	return backend.backend.PruneIdle()
}

func (backend *SupervisedMCPBackend) Close() error {
	if backend == nil {
		return nil
	}
	backend.gate.Lock()
	defer backend.gate.Unlock()
	if backend.closed {
		return nil
	}
	backend.closed = true
	if backend.backend == nil {
		return nil
	}
	return backend.backend.Close()
}

func (backend *SupervisedMCPBackend) Closed() bool {
	if backend == nil {
		return true
	}
	backend.gate.RLock()
	defer backend.gate.RUnlock()
	return backend.closed
}
