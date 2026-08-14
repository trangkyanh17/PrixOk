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

func runRewriteSupervisor() error {
	config := loadConfig()
	components := make([]supervisorComponent, 0, 3)

	if config.WatchdogEnabled {
		productionWatchdog := newWatchdog(config, execWatchdogRunner{}, isExecutableFile, log.Printf)
		components = append(components, supervisorComponent{
			Name: "watchdog",
			Run:  productionWatchdog.Run,
		})
	}

	if config.MCPLifecycleEnabled {
		if err := prepareMCPEnvironment(config.MCPPrewarmPlugins); err != nil {
			return err
		}
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
		components = append(components, supervisorComponent{Name: "mcp", Run: lifecycle.Run})
	}

	if config.TelegramShadowEnabled {
		components = append(components, supervisorComponent{
			Name: "telegram-shadow",
			Run: func(ctx context.Context) error {
				return runTelegramShadowIngress(ctx, config)
			},
		})
	}

	if len(components) == 0 {
		return nil
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return runSupervisorComponentsWithTimeout(ctx, components, config.ShutdownTimeout)
}

func main() {
	if err := configureLogTimezone(); err != nil {
		log.Printf("log timezone config: %v", err)
	}
	if err := runRewriteSupervisor(); err != nil {
		log.Printf("rewrite supervisor stopped with error: %v", err)
		os.Exit(1)
	}
}
