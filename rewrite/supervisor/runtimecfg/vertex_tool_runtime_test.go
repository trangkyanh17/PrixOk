package runtimecfg

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

func vertexRuntimeToken(context.Context, bool) (string, error) {
	return "token", nil
}

func noVertexSleep(context.Context, time.Duration) error {
	return nil
}

func TestVertexToolRuntimeExecutesToolThenReturnsText(t *testing.T) {
	requests := 0
	var secondPayload map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode: %v", err)
		}
		if requests == 1 {
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"working"},{"functionCall":{"name":"weather","args":{"city":"Hanoi"}},"thoughtSignature":"sig"}]}}]}`))
			return
		}
		secondPayload = payload
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"done"}]}}]}`))
	}))
	defer server.Close()

	stages := []int{}
	runtime := VertexToolRuntime{
		Client:        server.Client(),
		URL:           server.URL,
		TokenProvider: vertexRuntimeToken,
		Sleep:         noVertexSleep,
		ToolExecutor: func(ctx context.Context, name string, arguments map[string]any) (any, error) {
			if name != "weather" || arguments["city"] != "Hanoi" {
				t.Fatalf("name=%q args=%v", name, arguments)
			}
			return map[string]any{"ok": true, "temperature": 30}, nil
		},
		ProgressCallback: func(stage int, text string) error {
			stages = append(stages, stage)
			if stage == 1 && text != "working" {
				t.Fatalf("progress text=%q", text)
			}
			return nil
		},
		MaxContinuationRounds: 2,
	}

	text, err := runtime.Generate(context.Background(), map[string]any{
		"contents": []any{map[string]any{"role": "user", "parts": []any{map[string]any{"text": "weather?"}}}},
	})
	if err != nil || text != "done" {
		t.Fatalf("text=%q err=%v", text, err)
	}
	if requests != 2 || len(stages) != 2 || stages[0] != 1 || stages[1] != 2 {
		t.Fatalf("requests=%d stages=%v", requests, stages)
	}

	contents := secondPayload["contents"].([]any)
	if len(contents) != 3 {
		t.Fatalf("contents=%v", contents)
	}
	modelContent := contents[1].(map[string]any)
	modelParts := modelContent["parts"].([]any)
	if modelParts[1].(map[string]any)["thoughtSignature"] != "sig" {
		t.Fatalf("thoughtSignature not preserved: %v", modelParts[1])
	}
	functionResponse := contents[2].(map[string]any)["parts"].([]any)[0].(map[string]any)["functionResponse"].(map[string]any)
	if functionResponse["name"] != "weather" {
		t.Fatalf("functionResponse=%v", functionResponse)
	}
	result := functionResponse["response"].(map[string]any)["result"].(map[string]any)
	if result["temperature"] != float64(30) && result["temperature"] != 30 {
		t.Fatalf("result=%v", result)
	}
}

func TestVertexToolRuntimeParallelCodeToolsPreserveFunctionOrder(t *testing.T) {
	requests := 0
	var responseNames []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		var payload map[string]any
		_ = json.NewDecoder(r.Body).Decode(&payload)
		if requests == 1 {
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"slow","args":{}}},{"functionCall":{"name":"fast","args":{}}},{"functionCall":{"name":"mid","args":{}}}]}}]}`))
			return
		}
		contents := payload["contents"].([]any)
		parts := contents[len(contents)-1].(map[string]any)["parts"].([]any)
		for _, partValue := range parts {
			response := partValue.(map[string]any)["functionResponse"].(map[string]any)
			responseNames = append(responseNames, response["name"].(string))
		}
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"ok"}]}}]}`))
	}))
	defer server.Close()

	var mu sync.Mutex
	finished := []string{}
	runtime := VertexToolRuntime{
		Client:              server.Client(),
		URL:                 server.URL,
		TokenProvider:       vertexRuntimeToken,
		Sleep:               noVertexSleep,
		Mode:                "code",
		CodeToolConcurrency: 3,
		ToolExecutor: func(ctx context.Context, name string, arguments map[string]any) (any, error) {
			delay := map[string]time.Duration{"slow": 30 * time.Millisecond, "fast": 2 * time.Millisecond, "mid": 10 * time.Millisecond}[name]
			time.Sleep(delay)
			mu.Lock()
			finished = append(finished, name)
			mu.Unlock()
			return map[string]any{"ok": true, "name": name}, nil
		},
		MaxContinuationRounds: 2,
	}

	text, err := runtime.Generate(context.Background(), map[string]any{"contents": []any{}})
	if err != nil || text != "ok" {
		t.Fatalf("text=%q err=%v", text, err)
	}
	if strings.Join(responseNames, ",") != "slow,fast,mid" {
		t.Fatalf("response order=%v", responseNames)
	}
	mu.Lock()
	finishedOrder := strings.Join(finished, ",")
	mu.Unlock()
	if finishedOrder == "slow,fast,mid" {
		t.Fatalf("test did not exercise out-of-order completion: %v", finished)
	}
}

func TestVertexToolRuntimeSanitizesToolResultsAndConvertsErrors(t *testing.T) {
	requests := 0
	var firstResult map[string]any
	var secondResult map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		var payload map[string]any
		_ = json.NewDecoder(r.Body).Decode(&payload)
		if requests == 1 {
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"schema","args":{}}},{"functionCall":{"name":"broken","args":{}}}]}}]}`))
			return
		}
		contents := payload["contents"].([]any)
		parts := contents[len(contents)-1].(map[string]any)["parts"].([]any)
		firstResult = parts[0].(map[string]any)["functionResponse"].(map[string]any)["response"].(map[string]any)["result"].(map[string]any)
		secondResult = parts[1].(map[string]any)["functionResponse"].(map[string]any)["response"].(map[string]any)["result"].(map[string]any)
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"done"}]}}]}`))
	}))
	defer server.Close()

	runtime := VertexToolRuntime{
		Client:        server.Client(),
		URL:           server.URL,
		TokenProvider: vertexRuntimeToken,
		Sleep:         noVertexSleep,
		ToolExecutor: func(ctx context.Context, name string, arguments map[string]any) (any, error) {
			if name == "broken" {
				return nil, errors.New("boom")
			}
			return map[string]any{
				"ok":   true,
				"$ref": "#/defs/x",
				"nested": map[string]any{
					"$defs": map[string]any{"x": 1},
				},
			}, nil
		},
		MaxContinuationRounds: 2,
	}
	_, err := runtime.Generate(context.Background(), map[string]any{"contents": []any{}})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := firstResult["$ref"]; ok || firstResult["jsonschema_ref"] != "#/defs/x" {
		t.Fatalf("firstResult=%v", firstResult)
	}
	nested := firstResult["nested"].(map[string]any)
	if _, ok := nested["$defs"]; ok || nested["jsonschema_defs"] == nil {
		t.Fatalf("nested=%v", nested)
	}
	if secondResult["ok"] != false || !strings.Contains(secondResult["error"].(string), "boom") {
		t.Fatalf("secondResult=%v", secondResult)
	}
}

func TestVertexToolRuntimeForceGitHubMCPSequence(t *testing.T) {
	requests := 0
	forcedModes := []string{}
	allowedNames := []string{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		var payload map[string]any
		_ = json.NewDecoder(r.Body).Decode(&payload)
		toolConfig := payload["toolConfig"].(map[string]any)["functionCallingConfig"].(map[string]any)
		mode := toolConfig["mode"].(string)
		forcedModes = append(forcedModes, mode)
		if names, ok := toolConfig["allowedFunctionNames"].([]any); ok && len(names) > 0 {
			allowedNames = append(allowedNames, names[0].(string))
		} else {
			allowedNames = append(allowedNames, "")
		}

		switch requests {
		case 1:
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"code_plugin_search","args":{"query":"repo"}}}]}}]}`))
		case 2:
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"code_plugin_call","args":{"tool":"fetch"}}}]}}]}`))
		default:
			_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"final"}]}}]}`))
		}
	}))
	defer server.Close()

	seenPlugin := []string{}
	runtime := VertexToolRuntime{
		Client:           server.Client(),
		URL:              server.URL,
		TokenProvider:    vertexRuntimeToken,
		Sleep:            noVertexSleep,
		Mode:             "code",
		ForceGitHubMCP:   true,
		ToolExecutor: func(ctx context.Context, name string, arguments map[string]any) (any, error) {
			seenPlugin = append(seenPlugin, stringField(arguments["plugin"]))
			return map[string]any{"ok": true}, nil
		},
		MaxContinuationRounds: 2,
	}
	text, err := runtime.Generate(context.Background(), map[string]any{"contents": []any{}})
	if err != nil || text != "final" {
		t.Fatalf("text=%q err=%v", text, err)
	}
	if strings.Join(forcedModes, ",") != "ANY,ANY,AUTO" {
		t.Fatalf("modes=%v", forcedModes)
	}
	if strings.Join(allowedNames, ",") != "code_plugin_search,code_plugin_call," {
		t.Fatalf("allowed=%v", allowedNames)
	}
	if strings.Join(seenPlugin, ",") != "github,github" {
		t.Fatalf("plugins=%v", seenPlugin)
	}
}

func TestVertexToolRuntimeDirectGitHubPluginSkipsDiscoveryForce(t *testing.T) {
	requests := 0
	var firstAllowed string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		var payload map[string]any
		_ = json.NewDecoder(r.Body).Decode(&payload)
		if requests == 1 {
			config := payload["toolConfig"].(map[string]any)["functionCallingConfig"].(map[string]any)
			firstAllowed = config["allowedFunctionNames"].([]any)[0].(string)
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"code_plugin_call","args":{"tool":"fetch"}}}]}}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"ok"}]}}]}`))
	}))
	defer server.Close()

	runtime := VertexToolRuntime{
		Client:           server.Client(),
		URL:              server.URL,
		TokenProvider:    vertexRuntimeToken,
		Sleep:            noVertexSleep,
		Mode:             "code",
		ForceGitHubMCP:   true,
		DirectPluginName: "github",
		ToolExecutor: func(ctx context.Context, name string, arguments map[string]any) (any, error) {
			return map[string]any{"ok": true}, nil
		},
		MaxContinuationRounds: 2,
	}
	_, err := runtime.Generate(context.Background(), map[string]any{"contents": []any{}})
	if err != nil {
		t.Fatal(err)
	}
	if firstAllowed != "code_plugin_call" {
		t.Fatalf("first allowed=%q", firstAllowed)
	}
}

func TestVertexToolRuntimeExhaustsToolRoundBudget(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"loop","args":{}}}]}}]}`))
	}))
	defer server.Close()

	runtime := VertexToolRuntime{
		Client:                server.Client(),
		URL:                   server.URL,
		TokenProvider:         vertexRuntimeToken,
		Sleep:                 noVertexSleep,
		Mode:                  "chat",
		MaxContinuationRounds: 2,
		ToolExecutor: func(ctx context.Context, name string, arguments map[string]any) (any, error) {
			return map[string]any{"ok": true}, nil
		},
	}
	_, err := runtime.Generate(context.Background(), map[string]any{"contents": []any{}})
	if err == nil || !strings.Contains(err.Error(), "vượt quá 3 vòng gọi công cụ") {
		t.Fatalf("err=%v", err)
	}
	if requests != 8 {
		t.Fatalf("requests=%d want=8", requests)
	}
}

func TestVertexToolRuntimeConcurrencyAndTimeoutConfigurationClamps(t *testing.T) {
	runtime := VertexToolRuntime{Mode: "code", CodeToolConcurrency: 99, CodeToolTimeout: time.Second}
	if runtime.codeToolConcurrency() != 8 {
		t.Fatalf("concurrency=%d", runtime.codeToolConcurrency())
	}
	if runtime.codeToolTimeout() != 10*time.Second {
		t.Fatalf("timeout=%v", runtime.codeToolTimeout())
	}
}
