package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"prixok/atri-supervisor/runtimecfg"
)

func nextBackoff(current, minimum, maximum time.Duration) time.Duration {
	if current < minimum {
		return minimum
	}
	next := current * 2
	if next > maximum {
		return maximum
	}
	return next
}

func main() {
	config := loadConfig()
	if !config.MCPLifecycleEnabled {
		return
	}

	backend := runtimecfg.NewMCPTransportBackend(nil)
	backend.RequestTimeout = config.MCPRequestTimeout
	backend.IdleTTL = config.MCPIdleTTL

	lifecycle := newMCPLifecycle(backend, mcpLifecycleConfig{
		Plugins:     config.MCPPrewarmPlugins,
		Concurrency: config.MCPPrewarmConcurrency,
		Timeout:     config.MCPPrewarmTimeout,
		HealthEvery: config.MCPHealthInterval,
		PruneEvery:  config.MCPPruneInterval,
	}, log.Printf)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := lifecycle.Run(ctx); err != nil {
		log.Printf("MCP lifecycle stopped with error: %v", err)
	}
}
