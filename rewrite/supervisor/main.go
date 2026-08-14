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
	components := make([]supervisorComponent, 0, 2)

	if config.WatchdogEnabled {
		productionWatchdog := newWatchdog(config, execWatchdogRunner{}, isExecutableFile, log.Printf)
		components = append(components, supervisorComponent{
			Name: "watchdog",
			Run:  productionWatchdog.Run,
		})
	}

	if config.MCPLifecycleEnabled {
		transportBackend := runtimecfg.NewMCPTransportBackend(nil)
		transportBackend.RequestTimeout = config.MCPRequestTimeout
		transportBackend.IdleTTL = config.MCPIdleTTL
		backend := runtimecfg.NewSupervisedMCPBackend(transportBackend)

		lifecycle := newMCPLifecycle(backend, mcpLifecycleConfig{
			Plugins:     config.MCPPrewarmPlugins,
			Concurrency: config.MCPPrewarmConcurrency,
			Timeout:     config.MCPPrewarmTimeout,
			HealthEvery: config.MCPHealthInterval,
			PruneEvery:  config.MCPPruneInterval,
		}, log.Printf)
		components = append(components, supervisorComponent{
			Name: "mcp",
			Run:  lifecycle.Run,
		})
	}

	if len(components) == 0 {
		return
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := runSupervisorComponents(ctx, components); err != nil {
		log.Printf("rewrite supervisor stopped with error: %v", err)
	}
}
