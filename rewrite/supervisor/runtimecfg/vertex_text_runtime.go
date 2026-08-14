package runtimecfg

import (
	"context"
	"fmt"
	"strings"
)

type VertexTextRuntime struct {
	Client                HTTPDoer
	URL                   string
	TokenProvider         VertexTokenProvider
	Sleep                 VertexSleepFunc
	MaxContinuationRounds int
	MaxEmptyTextRetries   int
}

func cloneAnyMap(input map[string]any) map[string]any {
	output := make(map[string]any, len(input))
	for key, value := range input {
		switch typed := value.(type) {
		case map[string]any:
			output[key] = cloneAnyMap(typed)
		case []any:
			items := make([]any, len(typed))
			for index, item := range typed {
				if object, ok := item.(map[string]any); ok {
					items[index] = cloneAnyMap(object)
				} else {
					items[index] = item
				}
			}
			output[key] = items
		default:
			output[key] = value
		}
	}
	return output
}

func appendVertexContents(payload map[string]any, contents []map[string]any) error {
	current, ok := payload["contents"].([]any)
	if !ok {
		if typed, typedOK := payload["contents"].([]map[string]any); typedOK {
			current = make([]any, 0, len(typed)+len(contents))
			for _, item := range typed {
				current = append(current, item)
			}
		} else if payload["contents"] == nil {
			current = []any{}
		} else {
			return fmt.Errorf("vertex payload contents must be an array")
		}
	}
	for _, item := range contents {
		current = append(current, item)
	}
	payload["contents"] = current
	return nil
}

func (runtime VertexTextRuntime) Generate(
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

	for {
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
		if calls := ExtractVertexFunctionCalls(response); len(calls) > 0 {
			return "", fmt.Errorf("vertex text runtime received function calls")
		}

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
		case VertexTextContinue:
			if err := appendVertexContents(payload, result.AppendContents); err != nil {
				return "", err
			}
		default:
			return "", fmt.Errorf("unknown vertex text flow action: %s", result.Action)
		}
	}
}
