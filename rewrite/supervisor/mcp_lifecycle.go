package main

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
	"unicode"
)

const (
	defaultMCPHealthInterval = 30 * time.Minute
	defaultMCPPruneInterval  = 5 * time.Minute
	defaultMCPPrewarmTimeout = 90 * time.Second
)

type mcpLifecycleBackend interface {
	Prewarm(context.Context, []string, int) map[string]string
	PruneIdle() int
	Close() error
}

type mcpLifecycleConfig struct {
	Plugins     []string
	Concurrency int
	Timeout     time.Duration
	HealthEvery time.Duration
	PruneEvery  time.Duration
}

type mcpLifecycle struct {
	backend mcpLifecycleBackend
	config  mcpLifecycleConfig
	logf    func(string, ...any)
}

func newMCPLifecycle(
	backend mcpLifecycleBackend,
	config mcpLifecycleConfig,
	logf func(string, ...any),
) *mcpLifecycle {
	return &mcpLifecycle{backend: backend, config: config, logf: logf}
}

func (lifecycle *mcpLifecycle) normalizedConfig() mcpLifecycleConfig {
	config := mcpLifecycleConfig{}
	if lifecycle != nil {
		config = lifecycle.config
	}
	config.Plugins = append([]string(nil), config.Plugins...)
	if config.Concurrency <= 0 {
		config.Concurrency = 2
	}
	if config.Timeout <= 0 {
		config.Timeout = defaultMCPPrewarmTimeout
	}
	if config.HealthEvery <= 0 {
		config.HealthEvery = defaultMCPHealthInterval
	}
	if config.PruneEvery <= 0 {
		config.PruneEvery = defaultMCPPruneInterval
	}
	return config
}

func sanitizeMCPStatus(value string) string {
	value = strings.Map(func(char rune) rune {
		if unicode.IsControl(char) {
			return ' '
		}
		return char
	}, value)
	value = strings.Join(strings.Fields(value), " ")
	const limit = 240
	runes := []rune(value)
	if len(runes) <= limit {
		return value
	}
	return string(runes[:limit]) + "..."
}

func formatMCPResults(results map[string]string) string {
	if len(results) == 0 {
		return "no plugins"
	}
	plugins := make([]string, 0, len(results))
	for plugin := range results {
		plugins = append(plugins, plugin)
	}
	sort.Strings(plugins)
	parts := make([]string, 0, len(plugins))
	for _, plugin := range plugins {
		label := sanitizeMCPStatus(plugin)
		if label == "" {
			label = "mcp"
		}
		parts = append(parts, fmt.Sprintf("%s=%s", label, sanitizeMCPStatus(results[plugin])))
	}
	return strings.Join(parts, " ")
}

func (lifecycle *mcpLifecycle) prewarm(ctx context.Context, phase string) {
	config := lifecycle.normalizedConfig()
	probeCtx, cancel := context.WithTimeout(ctx, config.Timeout)
	defer cancel()
	results := lifecycle.backend.Prewarm(probeCtx, config.Plugins, config.Concurrency)
	if lifecycle.logf != nil {
		lifecycle.logf("MCP lifecycle %s: %s", phase, formatMCPResults(results))
	}
}

func (lifecycle *mcpLifecycle) runWithTicks(
	ctx context.Context,
	health <-chan time.Time,
	prune <-chan time.Time,
) (err error) {
	if lifecycle == nil || lifecycle.backend == nil {
		return errors.New("MCP lifecycle backend is not configured")
	}
	if ctx == nil {
		ctx = context.Background()
	}
	defer func() {
		if closeErr := lifecycle.backend.Close(); closeErr != nil {
			err = errors.Join(err, fmt.Errorf("close MCP lifecycle: %w", closeErr))
		}
	}()

	lifecycle.prewarm(ctx, "startup")
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-health:
			lifecycle.prewarm(ctx, "health")
		case <-prune:
			if pruned := lifecycle.backend.PruneIdle(); pruned > 0 && lifecycle.logf != nil {
				lifecycle.logf("MCP lifecycle pruned %d idle session(s)", pruned)
			}
		}
	}
}

func (lifecycle *mcpLifecycle) Run(ctx context.Context) error {
	if lifecycle == nil || lifecycle.backend == nil {
		return errors.New("MCP lifecycle backend is not configured")
	}
	config := lifecycle.normalizedConfig()
	healthTicker := time.NewTicker(config.HealthEvery)
	pruneTicker := time.NewTicker(config.PruneEvery)
	defer healthTicker.Stop()
	defer pruneTicker.Stop()
	return lifecycle.runWithTicks(ctx, healthTicker.C, pruneTicker.C)
}
