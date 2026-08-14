package runtimecfg

import (
	"context"
	"errors"
	"reflect"
	"testing"
)

type statusMCPBackend struct {
	*fakeMCPBackend
	ready  map[string]bool
	reason map[string]string
}

func (backend *statusMCPBackend) PluginStatus(
	_ context.Context,
	plugin string,
) (bool, string) {
	return backend.ready[plugin], backend.reason[plugin]
}

func TestMCPRuntimeStatusUsesAvailabilityAndProbe(t *testing.T) {
	backend := &statusMCPBackend{
		fakeMCPBackend: testMCPBackend(),
		ready:          map[string]bool{"github": true},
		reason:         map[string]string{"github": "configured"},
	}
	runtime := &MCPRuntime{Backend: backend}

	result := runtime.Status(context.Background(), "github", true)
	if result["ok"] != true || result["write_enabled"] != false {
		t.Fatalf("status=%v", result)
	}
	plugins := result["plugins"].(map[string]any)
	github := plugins["github"].(map[string]any)
	if github["ready"] != true || github["probe"] != "ok" || github["tool_count"] != 1 {
		t.Fatalf("github=%v", github)
	}
	if backend.listCalls["github"] != 1 {
		t.Fatalf("list calls=%v", backend.listCalls)
	}
}

func TestMCPRuntimeStatusReportsProbeFailure(t *testing.T) {
	base := testMCPBackend()
	base.listError = errors.New("list failed")
	backend := &statusMCPBackend{
		fakeMCPBackend: base,
		ready:          map[string]bool{"github": true},
		reason:         map[string]string{"github": "configured"},
	}
	runtime := &MCPRuntime{Backend: backend}
	result := runtime.Status(context.Background(), "github", true)
	github := result["plugins"].(map[string]any)["github"].(map[string]any)
	if github["probe"] != "failed" || github["error"] == nil {
		t.Fatalf("github=%v", github)
	}
}

func TestMCPBatchValidationPolicyAndStopBehavior(t *testing.T) {
	backend := testMCPBackend()
	runtime := &MCPRuntime{Backend: backend}
	steps := []map[string]any{
		{"tool": "get_file_contents", "arguments": map[string]any{"path": "README.md"}},
		{"tool": "create_issue", "arguments": map[string]any{"title": "blocked"}},
		{"tool": "get_file_contents", "arguments": map[string]any{"path": "README.md"}},
	}

	stopped := runtime.Batch(context.Background(), "github", steps, true)
	stoppedResults := stopped["results"].([]any)
	if stopped["ok"] != false || len(stoppedResults) != 2 || backend.callCount != 1 {
		t.Fatalf("stopped=%v call_count=%d", stopped, backend.callCount)
	}
	if stoppedResults[1].(map[string]any)["ok"] != false {
		t.Fatalf("blocked result=%v", stoppedResults[1])
	}

	backend.callCount = 0
	continued := runtime.Batch(context.Background(), "github", steps, false)
	continuedResults := continued["results"].([]any)
	if continued["ok"] != false || len(continuedResults) != 3 || backend.callCount != 2 {
		t.Fatalf("continued=%v call_count=%d", continued, backend.callCount)
	}
}

func TestMCPBatchStepParser(t *testing.T) {
	steps, err := mcpBatchSteps(`[
		{"tool":"one","arguments":{"x":1}},
		{"tool":"two","arguments":{}}
	]`)
	if err != nil {
		t.Fatal(err)
	}
	if len(steps) != 2 || steps[0]["tool"] != "one" || steps[1]["tool"] != "two" {
		t.Fatalf("steps=%v", steps)
	}
	if _, err := mcpBatchSteps(`{"tool":"one"}`); err == nil {
		t.Fatal("object JSON should not decode as steps array")
	}
}

type context7MCPBackend struct {
	calls []string
}

func (backend *context7MCPBackend) ListTools(
	context.Context,
	string,
) ([]MCPTool, error) {
	return nil, nil
}

func (backend *context7MCPBackend) CallTool(
	_ context.Context,
	plugin string,
	tool string,
	arguments map[string]any,
) (MCPCallResult, error) {
	backend.calls = append(backend.calls, plugin+"/"+tool)
	switch tool {
	case "resolve-library-id":
		if arguments["libraryName"] != "httpx" {
			return MCPCallResult{}, errors.New("unexpected library")
		}
		return MCPCallResult{
			Structured: map[string]any{"libraryId": "/encode/httpx"},
		}, nil
	case "query-docs":
		if arguments["libraryId"] != "/encode/httpx" {
			return MCPCallResult{}, errors.New("unexpected library ID")
		}
		return MCPCallResult{
			Content: []any{"HTTPX client docs"},
			Structured: map[string]any{
				"libraryId": arguments["libraryId"],
				"query":     arguments["query"],
			},
		}, nil
	default:
		return MCPCallResult{}, errors.New("unknown tool")
	}
}

func TestMCPContext7FastpathResolvesCachesAndQueries(t *testing.T) {
	backend := &context7MCPBackend{}
	runtime := &MCPRuntime{Backend: backend}
	fastpath := &MCPContext7Fastpath{Runtime: runtime}

	first := fastpath.Query(context.Background(), "httpx", "timeouts")
	if first["ok"] != true || first["library_id"] != "/encode/httpx" || first["cache_hit"] != false {
		t.Fatalf("first=%v", first)
	}
	if !reflect.DeepEqual(backend.calls, []string{"context7/resolve-library-id", "context7/query-docs"}) {
		t.Fatalf("calls=%v", backend.calls)
	}

	second := fastpath.Query(context.Background(), "httpx", "streaming")
	if second["ok"] != true || second["cache_hit"] != true {
		t.Fatalf("second=%v", second)
	}
	if !reflect.DeepEqual(backend.calls, []string{
		"context7/resolve-library-id",
		"context7/query-docs",
		"context7/query-docs",
	}) {
		t.Fatalf("calls=%v", backend.calls)
	}
}

func TestMCPFeatureAndAllToolRegistration(t *testing.T) {
	runtime := &MCPRuntime{Backend: testMCPBackend()}
	registry := NewToolRegistry()
	if err := RegisterAllMCPTools(registry, runtime); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{
		"code_context7_docs",
		"code_plugin_search",
		"code_plugin_call",
		"code_plugin_batch",
		"code_plugin_status",
	} {
		if !registry.Has(name) {
			t.Fatalf("missing tool %s", name)
		}
	}
	if got := len(registry.Declarations("code", false)); got != 5 {
		t.Fatalf("code declarations=%d", got)
	}
	if got := len(registry.Declarations("chat", false)); got != 0 {
		t.Fatalf("chat declarations=%d", got)
	}
}
