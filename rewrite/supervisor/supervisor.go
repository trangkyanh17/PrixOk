package main

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

const defaultSupervisorShutdownTimeout = 15 * time.Second

type supervisorComponent struct {
	Name string
	Run  func(context.Context) error
}

type supervisorResult struct {
	index int
	name  string
	err   error
}

func runSupervisorComponents(ctx context.Context, components []supervisorComponent) error {
	return runSupervisorComponentsWithTimeout(ctx, components, defaultSupervisorShutdownTimeout)
}

func runSupervisorComponentsWithTimeout(
	ctx context.Context,
	components []supervisorComponent,
	shutdownTimeout time.Duration,
) error {
	if ctx == nil {
		ctx = context.Background()
	}
	if len(components) == 0 {
		return nil
	}
	for _, component := range components {
		if strings.TrimSpace(component.Name) == "" || component.Run == nil {
			return errors.New("supervisor component is invalid")
		}
	}
	if shutdownTimeout <= 0 {
		shutdownTimeout = defaultSupervisorShutdownTimeout
	}

	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	results := make(chan supervisorResult, len(components))
	pending := make(map[int]string, len(components))
	for index, component := range components {
		index := index
		component := component
		pending[index] = component.Name
		go func() {
			results <- supervisorResult{index: index, name: component.Name, err: component.Run(runCtx)}
		}()
	}

	var joined error
	var shutdownTimer *time.Timer
	var shutdown <-chan time.Time
	startShutdown := func() {
		cancel()
		if shutdownTimer == nil {
			shutdownTimer = time.NewTimer(shutdownTimeout)
			shutdown = shutdownTimer.C
		}
	}
	defer func() {
		if shutdownTimer != nil {
			shutdownTimer.Stop()
		}
	}()

	parentDone := ctx.Done()
	remaining := len(components)
	for remaining > 0 {
		select {
		case <-parentDone:
			parentDone = nil
			startShutdown()
		case result := <-results:
			delete(pending, result.index)
			remaining--
			parentStopping := ctx.Err() != nil
			componentCanceled := errors.Is(result.err, context.Canceled) || errors.Is(result.err, context.DeadlineExceeded)
			intentionalCancellation := componentCanceled && (parentStopping || runCtx.Err() != nil)
			if result.err != nil && !intentionalCancellation {
				joined = errors.Join(joined, fmt.Errorf("%s: %w", result.name, result.err))
			}

			if !parentStopping && runCtx.Err() == nil {
				if result.err == nil {
					joined = errors.Join(joined, fmt.Errorf("%s stopped unexpectedly", result.name))
				}
				startShutdown()
			}
		case <-shutdown:
			names := make([]string, 0, len(pending))
			for _, name := range pending {
				names = append(names, name)
			}
			sort.Strings(names)
			joined = errors.Join(joined, fmt.Errorf(
				"supervisor shutdown timed out after %s waiting for %s",
				shutdownTimeout,
				strings.Join(names, ", "),
			))
			return joined
		}
	}
	return joined
}
