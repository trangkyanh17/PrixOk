package runtimecfg

import (
	"context"
	"fmt"
	"strings"
)

type OrchestratorRequest struct {
	Mode                   string
	PublicText             string
	SystemInstruction      string
	MemoryContext          string
	Contents               []any
	GenerationConfig       map[string]any
	WorkerHistory          []map[string]any
	WorkerCurrentParts     []map[string]any
	ThinkingLevel          string
	WorkerSkillInstruction string
	SkillPrivateRoute      bool
	CodingSkillHint        bool
	ToolContext            ToolContext
	AllowPrivateTools      bool
	ProgressCallback       VertexProgressCallback
	CodeToolConcurrency    int
	CodeToolTimeoutSeconds int
	MaxContinuationRounds  int
	MaxEmptyTextRetries    int
	ForceGitHubMCP         bool
	DirectPluginName       string
}

type OrchestratorResult struct {
	Text              string
	Chunks            []string
	WorkerUsed        bool
	WorkerTaskType    string
	WorkerProvider    string
	WorkerModel       string
	VerifierVerdict   string
	WorkerRetried     bool
	SupervisorContext string
}

type AtriOrchestrator struct {
	FreePool      *FreePoolRuntime
	VertexService *VertexServiceRuntime
	Registry      *ToolRegistry
	VertexClient  HTTPDoer
	VertexSleep   VertexSleepFunc
	Progress      VertexProgressCallback
}

func appendSystemContext(base, extra string) string {
	base = strings.TrimSpace(base)
	extra = strings.TrimSpace(extra)
	if base == "" {
		return extra
	}
	if extra == "" {
		return base
	}
	return base + "\n\n" + extra
}

func workerCurrentParts(prompt string) []map[string]any {
	return []map[string]any{{"text": strings.TrimSpace(prompt)}}
}

func (orchestrator *AtriOrchestrator) workerGenerator() WorkerGenerateFunc {
	if orchestrator == nil || orchestrator.FreePool == nil {
		return nil
	}
	return func(
		ctx context.Context,
		taskType string,
		systemInstruction string,
		prompt string,
	) (*FreeReply, error) {
		return orchestrator.FreePool.GenerateFreeChat(ctx, FreeChatRequest{
			SystemInstruction: systemInstruction,
			CurrentParts:      workerCurrentParts(prompt),
			TaskType:          taskType,
			ThinkingLevel:     "medium",
		})
	}
}

func (orchestrator *AtriOrchestrator) verifierGenerator() WorkerVerifyFunc {
	if orchestrator == nil || orchestrator.VertexService == nil {
		return nil
	}
	return func(ctx context.Context, prompt string) (string, error) {
		runtime, err := orchestrator.VertexService.TextRuntime(
			orchestrator.VertexClient,
			orchestrator.VertexSleep,
			1,
			1,
		)
		if err != nil {
			return "", err
		}
		payload := BuildVertexPayload(VertexPayloadOptions{
			Contents: []any{
				map[string]any{
					"role": "user",
					"parts": []any{
						map[string]any{"text": prompt},
					},
				},
			},
			GenerationConfig: map[string]any{
				"temperature":     0,
				"maxOutputTokens": 512,
			},
		})
		return runtime.Generate(ctx, payload)
	}
}

func (orchestrator *AtriOrchestrator) Run(
	ctx context.Context,
	request OrchestratorRequest,
) (OrchestratorResult, error) {
	if orchestrator == nil || orchestrator.VertexService == nil {
		return OrchestratorResult{}, fmt.Errorf("vertex service runtime is required")
	}

	mode := normalizeToolMode(request.Mode)
	currentParts := request.WorkerCurrentParts
	if len(currentParts) == 0 && strings.TrimSpace(request.PublicText) != "" {
		currentParts = workerCurrentParts(request.PublicText)
	}

	workerResult := WorkerSupervisorResult{}
	if orchestrator.FreePool != nil {
		workerResult = RunWorkerSupervisor(
			ctx,
			request.PublicText,
			WorkerSupervisorOptions{
				Mode:                   mode,
				BaseMemoryContext:      request.MemoryContext,
				WorkerSkillInstruction: request.WorkerSkillInstruction,
				SkillPrivateRoute:      request.SkillPrivateRoute,
				CodingSkillHint:        request.CodingSkillHint,
			},
			func(ctx context.Context, taskType, systemInstruction, prompt string) (*FreeReply, error) {
				return orchestrator.FreePool.GenerateFreeChat(ctx, FreeChatRequest{
					SystemInstruction: systemInstruction,
					History:           request.WorkerHistory,
					CurrentParts:      workerCurrentParts(prompt),
					ThinkingLevel:     request.ThinkingLevel,
					TaskType:          taskType,
				})
			},
			orchestrator.verifierGenerator(),
		)
	}

	extraContext := request.MemoryContext
	if workerResult.UseWorker {
		extraContext = workerResult.SupervisorContext
	}
	finalSystem := appendSystemContext(request.SystemInstruction, extraContext)

	contents := cloneAnySlice(request.Contents)
	if len(contents) == 0 && len(currentParts) > 0 {
		parts := make([]any, 0, len(currentParts))
		for _, part := range currentParts {
			parts = append(parts, cloneAnyMap(part))
		}
		contents = []any{
			map[string]any{
				"role":  "user",
				"parts": parts,
			},
		}
	}

	payload := BuildRegistryVertexPayload(
		orchestrator.Registry,
		mode,
		request.AllowPrivateTools,
		finalSystem,
		contents,
		request.GenerationConfig,
	)

	var text string
	var err error
	if orchestrator.Registry == nil || len(orchestrator.Registry.Declarations(mode, request.AllowPrivateTools)) == 0 {
		runtime, runtimeErr := orchestrator.VertexService.TextRuntime(
			orchestrator.VertexClient,
			orchestrator.VertexSleep,
			request.MaxContinuationRounds,
			request.MaxEmptyTextRetries,
		)
		if runtimeErr != nil {
			return OrchestratorResult{}, runtimeErr
		}
		text, err = runtime.Generate(ctx, payload)
	} else {
		progress := request.ProgressCallback
		if progress == nil {
			progress = orchestrator.Progress
		}
		runtime, runtimeErr := orchestrator.VertexService.RegistryToolRuntime(
			orchestrator.Registry,
			VertexRegistryRuntimeOptions{
				Client:                orchestrator.VertexClient,
				Sleep:                 orchestrator.VertexSleep,
				Mode:                  mode,
				ToolContext:           request.ToolContext,
				AllowPrivateTools:     request.AllowPrivateTools,
				ProgressCallback:      progress,
				CodeToolConcurrency:   request.CodeToolConcurrency,
				CodeToolTimeout:       request.CodeToolTimeoutSeconds,
				MaxContinuationRounds: request.MaxContinuationRounds,
				MaxEmptyTextRetries:   request.MaxEmptyTextRetries,
				ForceGitHubMCP:        request.ForceGitHubMCP,
				DirectPluginName:      request.DirectPluginName,
			},
		)
		if runtimeErr != nil {
			return OrchestratorResult{}, runtimeErr
		}
		text, err = runtime.Generate(ctx, payload)
	}
	if err != nil {
		return OrchestratorResult{}, err
	}

	text = CleanPublicAnswer(text)
	return OrchestratorResult{
		Text:              text,
		Chunks:            SplitReplyChunks(text),
		WorkerUsed:        workerResult.UseWorker,
		WorkerTaskType:    workerResult.TaskType,
		WorkerProvider:    workerResult.WorkerProvider,
		WorkerModel:       workerResult.WorkerModel,
		VerifierVerdict:   workerResult.VerifierVerdict,
		WorkerRetried:     workerResult.Retried,
		SupervisorContext: workerResult.SupervisorContext,
	}, nil
}
