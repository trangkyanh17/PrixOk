package runtimecfg

import (
	"fmt"
	"strings"
	"time"
)

type VertexRegistryRuntimeOptions struct {
	Client                HTTPDoer
	Sleep                 VertexSleepFunc
	Mode                  string
	ToolContext           ToolContext
	AllowPrivateTools     bool
	ProgressCallback      VertexProgressCallback
	CodeToolConcurrency   int
	CodeToolTimeout       int
	MaxContinuationRounds int
	MaxEmptyTextRetries   int
	ForceGitHubMCP        bool
	DirectPluginName      string
}

func (service *VertexServiceRuntime) RegistryToolRuntime(
	registry *ToolRegistry,
	options VertexRegistryRuntimeOptions,
) (VertexToolRuntime, error) {
	if registry == nil {
		return VertexToolRuntime{}, fmt.Errorf("tool registry is required")
	}
	mode := normalizeToolMode(options.Mode)
	options.ToolContext.Mode = mode

	runtime, err := service.ToolRuntime(
		options.Client,
		options.Sleep,
		mode,
		registry.VertexExecutor(options.ToolContext, options.AllowPrivateTools),
	)
	if err != nil {
		return VertexToolRuntime{}, err
	}
	runtime.ProgressCallback = options.ProgressCallback
	runtime.CodeToolConcurrency = options.CodeToolConcurrency
	if options.CodeToolTimeout > 0 {
		runtime.CodeToolTimeout = time.Duration(options.CodeToolTimeout) * time.Second
	}
	runtime.MaxContinuationRounds = options.MaxContinuationRounds
	runtime.MaxEmptyTextRetries = options.MaxEmptyTextRetries
	runtime.ForceGitHubMCP = options.ForceGitHubMCP
	runtime.DirectPluginName = strings.TrimSpace(options.DirectPluginName)
	return runtime, nil
}
