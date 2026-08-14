package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
)

type supervisorComponent struct {
	Name string
	Run  func(context.Context) error
}

type supervisorResult struct {
	name string
	err  error
}

func runSupervisorComponents(ctx context.Context, components []supervisorComponent) error {
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

	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	results := make(chan supervisorResult, len(components))
	for _, component := range components {
		component := component
		go func() {
			results <- supervisorResult{name: component.Name, err: component.Run(runCtx)}
		}()
	}

	var joined error
	for range components {
		result := <-results
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
			cancel()
		}
	}
	return joined
}
