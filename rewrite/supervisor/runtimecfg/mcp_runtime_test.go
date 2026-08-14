package runtimecfg

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"
)

type fakeMCPBackend struct {
	tools      map[string][]MCPTool
	listCalls  map[string]int
	callCount  int
	lastPlugin string
	lastTool   string
	lastArgs   map[string]any
	listError  error
	callError  error
	callResult MCPCallResult
}

func (backend *fakeMCPBackend) ListTools(_ context.Context, plugin string) ([]MCPTool, error) {
	if backend.listCalls == nil {
		backend.listCalls = map[string]int{}
	}
	backend.listCalls[plugin]++
	if backend.listError != nil {
		return nil, backend.listError
	}
	return cloneMCPTools(backend.tools[plugin]), nil
}

func (backend *fakeMCPBackend) CallTool(
	_ context.Context,
	plugin string,
	tool string,
	arguments map[string]any,
) (MCPCallResult, error) {
	backend.callCount++
	backend.lastPlugin = plugin
	backend.lastTool = tool
	backend.lastArgs = cloneAnyMap(arguments)
	arguments["mutated"] = true
	if backend.callError != nil {
		return MCPCallResult{}, backend.callError
	}
	return backend.callResult, nil
}

func testMCPBackend() *fakeMCPBackend {
	return &fakeMCPBackend{
		tools: map[string][]MCPTool{
			"github": {
				{
					Name:        "get_file_contents",
					Description: "Read repository file contents",
					InputSchema: map[string]any{
						"type": "object",
						"properties": map[string]any{
							"path": map[string]any{"type": "string"},
						},
					},
				},
				{
					Name:        "create_issue",
					Description: "Create repository issue",
					InputSchema: map[string]any{"type": "object"},
				},
			},
			"context7": {
				{
					Name:        "query_docs",
					Description: "Read library API documentation",
					InputSchema: map[string]any{"type": "object"},
				},
			},
		},
		callResult: MCPCallResult{
			Content: []any{"ok", "see #/$defs/Target"},
			Structured: map[string]any{
				"value": 42,
				"$defs": map[string]any{"Target": map[string]any{"type": "string"}},
			},
		},
	}
}

func TestMCPRuntimeCachesDiscoveryAndFiltersUnsafeTools(t *testing.T) {
	backend := testMCPBackend()
	now := time.Unix(100, 0)
	runtime := &MCPRuntime{
		Backend:  backend,
		CacheTTL: time.Hour,
		Now: func() time.Time {
			return now
		},
	}

	first := runtime.Search(context.Background(), "github repository file", "", 10)
	if first["ok"] != true {
		t.Fatalf("first=%v", first)
	}
	tools := first["tools"].([]any)
	if len(tools) != 1 || tools[0].(map[string]any)["name"] != "get_file_contents" {
		t.Fatalf("tools=%v", tools)
	}
	if backend.listCalls["github"] != 1 {
		t.Fatalf("list calls=%v", backend.listCalls)
	}

	second := runtime.Search(context.Background(), "github repository file", "github", 10)
	if second["ok"] != true || backend.listCalls["github"] != 1 {
		t.Fatalf("second=%v calls=%v", second, backend.listCalls)
	}

	now = now.Add(2 * time.Hour)
	_ = runtime.Search(context.Background(), "github repository file", "github", 10)
	if backend.listCalls["github"] != 2 {
		t.Fatalf("expired cache calls=%v", backend.listCalls)
	}
}

func TestMCPRuntimeExactPluginFallbackAndDirectFastpath(t *testing.T) {
	backend := testMCPBackend()
	runtime := &MCPRuntime{Backend: backend}

	result := runtime.Search(context.Background(), "unrelated topic", "github", 10)
	if result["ok"] != true {
		t.Fatalf("result=%v", result)
	}
	if !reflect.DeepEqual(result["fallback_plugins"], []any{"github"}) {
		t.Fatalf("fallback=%v", result["fallback_plugins"])
	}
	tools := result["tools"].([]any)
	if len(tools) != 1 || tools[0].(map[string]any)["name"] != "get_file_contents" {
		t.Fatalf("tools=%v", tools)
	}

	catalog := runtime.DirectFastpath(context.Background(), "please use github mcp", 10)
	if catalog["ok"] != true || catalog["plugin"] != "github" || catalog["tool_count"] != 1 {
		t.Fatalf("catalog=%v", catalog)
	}
}

func TestMCPRuntimeCallPolicySanitizationAndArgumentClone(t *testing.T) {
	backend := testMCPBackend()
	runtime := &MCPRuntime{Backend: backend}
	arguments := map[string]any{"path": "README.md"}

	result := runtime.Call(context.Background(), "github", "get_file_contents", arguments)
	if result["ok"] != true || backend.callCount != 1 {
		t.Fatalf("result=%v count=%d", result, backend.callCount)
	}
	if _, mutated := arguments["mutated"]; mutated {
		t.Fatalf("caller arguments mutated: %v", arguments)
	}
	if backend.lastPlugin != "github" || backend.lastTool != "get_file_contents" {
		t.Fatalf("call=%s/%s", backend.lastPlugin, backend.lastTool)
	}
	structured := result["structured"].(map[string]any)
	if structured["value"] != 42 {
		t.Fatalf("structured=%v", structured)
	}
	if _, leaked := structured["$defs"]; leaked {
		t.Fatalf("schema metadata leaked: %v", structured)
	}
	content := result["content"].([]any)
	if content[1] != "see schema:Target" {
		t.Fatalf("content=%v", content)
	}

	blockedWrite := runtime.Call(context.Background(), "github", "create_issue", nil)
	if blockedWrite["ok"] != false || backend.callCount != 1 {
		t.Fatalf("write=%v count=%d", blockedWrite, backend.callCount)
	}
	blockedSecret := runtime.Call(
		context.Background(),
		"github",
		"get_file_contents",
		map[string]any{"path": "/app/.env"},
	)
	if blockedSecret["ok"] != false || backend.callCount != 1 {
		t.Fatalf("secret=%v count=%d", blockedSecret, backend.callCount)
	}
}

func TestMCPRuntimeErrorsAndRegistryBridge(t *testing.T) {
	backend := testMCPBackend()
	backend.callError = errors.New("backend failed")
	runtime := &MCPRuntime{Backend: backend}

	failed := runtime.Call(context.Background(), "github", "get_file_contents", nil)
	if failed["ok"] != false {
		t.Fatalf("failed=%v", failed)
	}

	registry := NewToolRegistry()
	if err := RegisterMCPTools(registry, runtime); err != nil {
		t.Fatal(err)
	}
	if !registry.Has("code_plugin_search") || !registry.Has("code_plugin_call") {
		t.Fatal("MCP tools not registered")
	}
	if len(registry.Declarations("chat", false)) != 0 {
		t.Fatal("MCP coding tools leaked into chat mode")
	}
	if len(registry.Declarations("code", false)) != 2 {
		t.Fatalf("code declarations=%v", registry.Declarations("code", false))
	}

	searchResult := registry.Execute(
		context.Background(),
		ToolContext{Mode: "code"},
		"code_plugin_search",
		map[string]any{"query": "github repository file", "plugin": "github", "limit": 5},
		false,
	).(map[string]any)
	if searchResult["ok"] != true {
		t.Fatalf("registry search=%v", searchResult)
	}
}
