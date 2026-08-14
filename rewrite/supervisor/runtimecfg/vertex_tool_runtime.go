package runtimecfg

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"
)

type VertexToolExecutor func(
	ctx context.Context,
	name string,
	arguments map[string]any,
) (any, error)

type VertexProgressCallback func(stage int, text string) error

type VertexToolRuntime struct {
	Client                HTTPDoer
	URL                   string
	TokenProvider         VertexTokenProvider
	Sleep                 VertexSleepFunc
	Mode                  string
	ToolExecutor          VertexToolExecutor
	ProgressCallback      VertexProgressCallback
	CodeToolConcurrency   int
	CodeToolTimeout       time.Duration
	MaxContinuationRounds int
	MaxEmptyTextRetries   int
	ForceGitHubMCP        bool
	DirectPluginName      string
}

type vertexToolCallResult struct {
	Part      map[string]any
	Name      string
	Arguments map[string]any
	ToolValue any
}

func cloneArguments(value any) map[string]any {
	arguments, ok := value.(map[string]any)
	if !ok || arguments == nil {
		return map[string]any{}
	}
	return cloneAnyMap(arguments)
}

func toolResultOK(value any) bool {
	object, ok := value.(map[string]any)
	if !ok {
		return false
	}
	result, ok := object["ok"].(bool)
	return ok && result
}

func (runtime VertexToolRuntime) mode() string {
	mode := strings.ToLower(strings.TrimSpace(runtime.Mode))
	if mode == "" {
		return "chat"
	}
	return mode
}

func (runtime VertexToolRuntime) maxToolRounds() int {
	if runtime.mode() == "code" {
		return 8
	}
	return 3
}

func (runtime VertexToolRuntime) codeToolConcurrency() int {
	value := runtime.CodeToolConcurrency
	if value <= 0 {
		value = 4
	}
	if value > 8 {
		value = 8
	}
	return value
}

func (runtime VertexToolRuntime) codeToolTimeout() time.Duration {
	value := runtime.CodeToolTimeout
	if value <= 0 {
		value = 60 * time.Second
	}
	if value < 10*time.Second {
		value = 10 * time.Second
	}
	if value > 120*time.Second {
		value = 120 * time.Second
	}
	return value
}

func forcedGitHubToolConfig(
	payload map[string]any,
	searchDone bool,
	callDone bool,
) {
	forcedName := ""
	if !searchDone {
		forcedName = "code_plugin_search"
	} else if !callDone {
		forcedName = "code_plugin_call"
	}
	if forcedName != "" {
		payload["toolConfig"] = map[string]any{
			"functionCallingConfig": map[string]any{
				"mode":                 "ANY",
				"allowedFunctionNames": []any{forcedName},
			},
		}
		return
	}
	payload["toolConfig"] = map[string]any{
		"functionCallingConfig": map[string]any{
			"mode": "AUTO",
		},
	}
}

func (runtime VertexToolRuntime) executeOneToolCall(
	ctx context.Context,
	functionCall map[string]any,
) vertexToolCallResult {
	name := strings.TrimSpace(stringField(functionCall["name"]))
	arguments := cloneArguments(functionCall["args"])
	if runtime.ForceGitHubMCP && runtime.mode() == "code" &&
		(name == "code_plugin_search" || name == "code_plugin_call") {
		arguments["plugin"] = "github"
	}

	var toolValue any
	if runtime.ToolExecutor == nil {
		toolValue = map[string]any{
			"ok":    false,
			"error": fmt.Sprintf("Công cụ %s gặp lỗi nội bộ: tool executor is not configured", name),
		}
	} else {
		callContext := ctx
		cancel := func() {}
		if runtime.mode() == "code" {
			callContext, cancel = context.WithTimeout(ctx, runtime.codeToolTimeout())
		}
		value, err := runtime.ToolExecutor(callContext, name, arguments)
		cancel()
		if err != nil {
			if runtime.mode() == "code" && callContext.Err() == context.DeadlineExceeded {
				toolValue = map[string]any{
					"ok": false,
					"error": fmt.Sprintf(
						"Công cụ %s quá thời gian %gs.",
						name,
						runtime.codeToolTimeout().Seconds(),
					),
				}
			} else {
				toolValue = map[string]any{
					"ok":    false,
					"error": fmt.Sprintf("Công cụ %s gặp lỗi nội bộ: %v", name, err),
				}
			}
		} else {
			toolValue = value
		}
	}

	return vertexToolCallResult{
		Name:      name,
		Arguments: arguments,
		ToolValue: toolValue,
		Part: map[string]any{
			"functionResponse": map[string]any{
				"name": name,
				"response": map[string]any{
					"result": VertexSafeToolResult(toolValue),
				},
			},
		},
	}
}

func (runtime VertexToolRuntime) executeToolCalls(
	ctx context.Context,
	functionCalls []map[string]any,
) []vertexToolCallResult {
	results := make([]vertexToolCallResult, len(functionCalls))
	if runtime.mode() != "code" || len(functionCalls) <= 1 {
		for index, call := range functionCalls {
			results[index] = runtime.executeOneToolCall(ctx, call)
		}
		return results
	}

	semaphore := make(chan struct{}, runtime.codeToolConcurrency())
	var waitGroup sync.WaitGroup
	for index, call := range functionCalls {
		index := index
		call := call
		waitGroup.Add(1)
		go func() {
			defer waitGroup.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()
			results[index] = runtime.executeOneToolCall(ctx, call)
		}()
	}
	waitGroup.Wait()
	return results
}

func (runtime VertexToolRuntime) progress(stage int, text string) {
	if runtime.ProgressCallback == nil {
		return
	}
	_ = runtime.ProgressCallback(stage, text)
}

func (runtime VertexToolRuntime) Generate(
	ctx context.Context,
	inputPayload map[string]any,
) (string, error) {
	if strings.TrimSpace(runtime.URL) == "" {
		return "", fmt.Errorf("vertex URL is required")
	}
	if runtime.TokenProvider == nil {
		return "", fmt.Errorf("vertex token provider is required")
	}

	payload := cloneAnyMap(inputPayload)
	flow := NewVertexTextFlowState(runtime.MaxContinuationRounds)
	if runtime.MaxEmptyTextRetries > 0 {
		flow.MaxEmptyTextRetries = runtime.MaxEmptyTextRetries
	}
	maxToolRounds := runtime.maxToolRounds()
	maxRequestRounds := maxToolRounds + flow.MaxContinuationRounds + flow.MaxEmptyTextRetries + 1

	githubPluginSearchDone := strings.EqualFold(strings.TrimSpace(runtime.DirectPluginName), "github")
	githubMCPCallDone := false

	for requestRound := 0; requestRound < maxRequestRounds; requestRound++ {
		if runtime.ForceGitHubMCP && runtime.mode() == "code" {
			forcedGitHubToolConfig(payload, githubPluginSearchDone, githubMCPCallDone)
		}

		response, err := PostVertex(
			ctx,
			runtime.Client,
			runtime.URL,
			payload,
			runtime.TokenProvider,
			runtime.Sleep,
		)
		if err != nil {
			return "", err
		}

		functionCalls := ExtractVertexFunctionCalls(response)
		if len(functionCalls) == 0 {
			result, err := flow.Process(response)
			if err != nil {
				return "", err
			}
			switch result.Action {
			case VertexTextDone:
				return result.Text, nil
			case VertexTextRetryEmpty:
				sleep := runtime.Sleep
				if sleep == nil {
					sleep = defaultVertexSleep
				}
				if err := sleep(ctx, result.RetryDelay); err != nil {
					return "", err
				}
				continue
			case VertexTextContinue:
				if err := appendVertexContents(payload, result.AppendContents); err != nil {
					return "", err
				}
				continue
			default:
				return "", fmt.Errorf("unknown vertex text flow action: %s", result.Action)
			}
		}

		runtime.progress(1, CleanPublicAnswer(ExtractVertexOptionalText(response)))
		toolResults := runtime.executeToolCalls(ctx, functionCalls)
		responseParts := make([]any, 0, len(toolResults))
		for _, result := range toolResults {
			responseParts = append(responseParts, result.Part)
			if runtime.ForceGitHubMCP && runtime.mode() == "code" && toolResultOK(result.ToolValue) {
				if result.Name == "code_plugin_search" {
					githubPluginSearchDone = true
				} else if result.Name == "code_plugin_call" &&
					strings.EqualFold(strings.TrimSpace(stringField(result.Arguments["plugin"])), "github") {
					githubMCPCallDone = true
				}
			}
		}
		runtime.progress(2, "")

		modelContent := vertexModelContent(response)
		appendContents := []map[string]any{}
		if modelContent != nil {
			appendContents = append(appendContents, modelContent)
		}
		appendContents = append(appendContents, map[string]any{
			"role":  "user",
			"parts": responseParts,
		})
		if err := appendVertexContents(payload, appendContents); err != nil {
			return "", err
		}
	}

	return "", fmt.Errorf("Em đã vượt quá %d vòng gọi công cụ trong một yêu cầu.", maxToolRounds)
}
