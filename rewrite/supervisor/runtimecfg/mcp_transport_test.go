package runtimecfg

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type fakeConcreteMCPTransport struct {
	mu         sync.Mutex
	initCount  int
	listCount  int
	callCount  int
	closeCount int
	failList   bool
	tools      []MCPTool
}

func (transport *fakeConcreteMCPTransport) Initialize(context.Context) error {
	transport.mu.Lock()
	transport.initCount++
	transport.mu.Unlock()
	return nil
}

func (transport *fakeConcreteMCPTransport) ListTools(context.Context) ([]MCPTool, error) {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	transport.listCount++
	if transport.failList {
		transport.failList = false
		return nil, errors.New("discovery failed")
	}
	return cloneMCPTools(transport.tools), nil
}

func (transport *fakeConcreteMCPTransport) CallTool(
	_ context.Context,
	tool string,
	arguments map[string]any,
) (MCPCallResult, error) {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	transport.callCount++
	arguments["mutated"] = true
	return MCPCallResult{
		Content:    []any{map[string]any{"type": "text", "text": tool}},
		Structured: map[string]any{"ok": true},
	}, nil
}

func (transport *fakeConcreteMCPTransport) Close() error {
	transport.mu.Lock()
	transport.closeCount++
	transport.mu.Unlock()
	return nil
}

func TestMCPTransportBackendReusesExpiresAndRetriesDiscovery(t *testing.T) {
	now := time.Unix(100, 0)
	created := []*fakeConcreteMCPTransport{}
	backend := NewMCPTransportBackend(nil)
	backend.Now = func() time.Time { return now }
	backend.IdleTTL = time.Hour
	backend.Factory = func(MCPPluginSpec) (MCPTransport, error) {
		transport := &fakeConcreteMCPTransport{
			tools: []MCPTool{{Name: "query_docs"}},
		}
		if len(created) == 0 {
			transport.failList = true
		}
		created = append(created, transport)
		return transport, nil
	}

	tools, err := backend.ListTools(context.Background(), "context7")
	if err != nil {
		t.Fatal(err)
	}
	if len(tools) != 1 || tools[0].Name != "query_docs" {
		t.Fatalf("tools=%v", tools)
	}
	if len(created) != 2 || created[0].closeCount != 1 {
		t.Fatalf("created=%d first=%+v", len(created), created[0])
	}

	arguments := map[string]any{"libraryId": "/a/b"}
	if _, err := backend.CallTool(context.Background(), "context7", "query-docs", arguments); err != nil {
		t.Fatal(err)
	}
	if _, mutated := arguments["mutated"]; mutated {
		t.Fatalf("caller arguments mutated: %v", arguments)
	}
	if len(created) != 2 || created[1].callCount != 1 {
		t.Fatalf("session was not reused: created=%d call=%d", len(created), created[1].callCount)
	}

	now = now.Add(2 * time.Hour)
	if pruned := backend.PruneIdle(); pruned != 1 {
		t.Fatalf("pruned=%d", pruned)
	}
	if created[1].closeCount != 1 {
		t.Fatalf("expired transport not closed: %+v", created[1])
	}
}

func TestMCPTransportBackendSpecsAndPrewarm(t *testing.T) {
	backend := NewMCPTransportBackend(map[string]string{
		"GITHUB_TOKEN":           "secret-token",
		"CONTEXT7_API_KEY":       "context-key",
		"ATRI_CODE_PROJECT_ROOT": "/workspace",
		"ATRI_UVX":               "/tmp/uvx",
	})
	backend.LookPath = func(command string) (string, error) {
		if command == "chromium" || command == "npx" {
			return "/usr/bin/" + command, nil
		}
		return "", errors.New("missing")
	}
	backend.Stat = func(path string) (os.FileInfo, error) {
		return nil, errors.New("missing: " + path)
	}

	specs := backend.PluginSpecs()
	if specs["github"].Headers["Authorization"] != "Bearer secret-token" {
		t.Fatalf("github headers=%v", specs["github"].Headers)
	}
	if specs["context7"].Headers["CONTEXT7_API_KEY"] != "context-key" {
		t.Fatalf("context7 headers=%v", specs["context7"].Headers)
	}
	if !strings.Contains(strings.Join(specs["serena"].Args, " "), "/workspace") {
		t.Fatalf("serena args=%v", specs["serena"].Args)
	}
	if !strings.Contains(strings.Join(specs["chrome-devtools"].Args, " "), "/usr/bin/chromium") {
		t.Fatalf("chrome args=%v", specs["chrome-devtools"].Args)
	}

	backend.Factory = func(spec MCPPluginSpec) (MCPTransport, error) {
		return &fakeConcreteMCPTransport{tools: []MCPTool{{Name: spec.Name + "_tool"}}}, nil
	}
	results := backend.Prewarm(context.Background(), []string{"context7", "github"}, 2)
	if results["context7"] != "ready:1" || results["github"] != "ready:1" {
		t.Fatalf("prewarm=%v", results)
	}
}

func TestHTTPMCPTransportSessionPaginationSSEAndClose(t *testing.T) {
	var initialized atomic.Bool
	var deleteSeen atomic.Bool
	var callHeaders []string
	var mu sync.Mutex

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		defer request.Body.Close()
		if request.Method == http.MethodDelete {
			if request.Header.Get("Mcp-Session-Id") != "session-1" {
				t.Errorf("delete session=%q", request.Header.Get("Mcp-Session-Id"))
			}
			deleteSeen.Store(true)
			writer.WriteHeader(http.StatusNoContent)
			return
		}

		var envelope map[string]any
		if err := json.NewDecoder(request.Body).Decode(&envelope); err != nil {
			t.Errorf("decode request: %v", err)
			writer.WriteHeader(http.StatusBadRequest)
			return
		}
		method, _ := envelope["method"].(string)
		id := envelope["id"]
		if method != "initialize" && method != "notifications/initialized" {
			mu.Lock()
			callHeaders = append(callHeaders,
				request.Header.Get("Mcp-Session-Id")+"|"+request.Header.Get("MCP-Protocol-Version"),
			)
			mu.Unlock()
		}

		switch method {
		case "initialize":
			writer.Header().Set("Content-Type", "application/json")
			writer.Header().Set("Mcp-Session-Id", "session-1")
			_ = json.NewEncoder(writer).Encode(map[string]any{
				"jsonrpc": "2.0",
				"id":      id,
				"result": map[string]any{
					"protocolVersion": defaultMCPProtocol,
					"capabilities":    map[string]any{},
					"serverInfo":      map[string]any{"name": "test", "version": "1"},
				},
			})
		case "notifications/initialized":
			if request.Header.Get("Mcp-Session-Id") != "session-1" {
				t.Errorf("initialized session=%q", request.Header.Get("Mcp-Session-Id"))
			}
			initialized.Store(true)
			writer.WriteHeader(http.StatusAccepted)
		case "tools/list":
			params, _ := envelope["params"].(map[string]any)
			cursor, _ := params["cursor"].(string)
			if cursor == "" {
				writer.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprintf(writer, "event: message\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\",\"params\":{}}\n\n")
				response := map[string]any{
					"jsonrpc": "2.0",
					"id":      id,
					"result": map[string]any{
						"tools": []any{map[string]any{
							"name":        "first",
							"description": "first tool",
							"inputSchema": map[string]any{"type": "object"},
						}},
						"nextCursor": "page-2",
					},
				}
				data, _ := json.Marshal(response)
				fmt.Fprintf(writer, "event: message\ndata: %s\n\n", data)
				return
			}
			writer.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(writer).Encode(map[string]any{
				"jsonrpc": "2.0",
				"id":      id,
				"result": map[string]any{
					"tools": []any{map[string]any{
						"name":        "second",
						"description": "second tool",
						"inputSchema": map[string]any{"type": "object"},
					}},
				},
			})
		case "tools/call":
			writer.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(writer).Encode(map[string]any{
				"jsonrpc": "2.0",
				"id":      id,
				"result": map[string]any{
					"content":           []any{map[string]any{"type": "text", "text": "ok"}},
					"structuredContent": map[string]any{"value": 42},
				},
			})
		default:
			writer.WriteHeader(http.StatusBadRequest)
		}
	}))
	defer server.Close()

	transport := newHTTPMCPTransport(MCPPluginSpec{
		Name:      "context7",
		Transport: "http",
		URL:       server.URL,
		Headers:   map[string]string{"X-Test": "yes"},
	}, server.Client())

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := transport.Initialize(ctx); err != nil {
		t.Fatal(err)
	}
	if !initialized.Load() {
		t.Fatal("initialized notification not received")
	}
	tools, err := transport.ListTools(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if names := []string{tools[0].Name, tools[1].Name}; !reflect.DeepEqual(names, []string{"first", "second"}) {
		t.Fatalf("tools=%v", names)
	}
	result, err := transport.CallTool(ctx, "first", map[string]any{"x": 1})
	if err != nil {
		t.Fatal(err)
	}
	structured := result.Structured.(map[string]any)
	if structured["value"] != float64(42) {
		t.Fatalf("structured=%v", structured)
	}
	if err := transport.Close(); err != nil {
		t.Fatal(err)
	}
	if !deleteSeen.Load() {
		t.Fatal("MCP session DELETE was not sent")
	}
	mu.Lock()
	defer mu.Unlock()
	for _, header := range callHeaders {
		if header != "session-1|"+defaultMCPProtocol {
			t.Fatalf("session/protocol header=%q", header)
		}
	}
}

func TestMCPStdioHelper(t *testing.T) {
	if os.Getenv("GO_WANT_MCP_STDIO_HELPER") != "1" {
		return
	}
	scanner := bufio.NewScanner(os.Stdin)
	encoder := json.NewEncoder(os.Stdout)
	for scanner.Scan() {
		var envelope map[string]any
		if err := json.Unmarshal(scanner.Bytes(), &envelope); err != nil {
			os.Exit(2)
		}
		method, _ := envelope["method"].(string)
		id := envelope["id"]
		switch method {
		case "initialize":
			_ = encoder.Encode(map[string]any{
				"jsonrpc": "2.0",
				"id":      id,
				"result": map[string]any{
					"protocolVersion": defaultMCPProtocol,
					"capabilities":    map[string]any{},
				},
			})
		case "notifications/initialized":
			continue
		case "tools/list":
			_ = encoder.Encode(map[string]any{
				"jsonrpc": "2.0",
				"method":  "notifications/progress",
				"params":  map[string]any{},
			})
			params, _ := envelope["params"].(map[string]any)
			cursor, _ := params["cursor"].(string)
			if cursor == "" {
				_ = encoder.Encode(map[string]any{
					"jsonrpc": "2.0",
					"id":      id,
					"result": map[string]any{
						"tools": []any{map[string]any{
							"name":        "one",
							"description": "one",
							"inputSchema": map[string]any{"type": "object"},
						}},
						"nextCursor": "next",
					},
				})
				continue
			}
			_ = encoder.Encode(map[string]any{
				"jsonrpc": "2.0",
				"id":      id,
				"result": map[string]any{
					"tools": []any{map[string]any{
						"name":        "two",
						"description": "two",
						"inputSchema": map[string]any{"type": "object"},
					}},
				},
			})
		case "tools/call":
			_ = encoder.Encode(map[string]any{
				"jsonrpc": "2.0",
				"id":      "server-request",
				"method":  "roots/list",
				"params":  map[string]any{},
			})
			if !scanner.Scan() {
				os.Exit(3)
			}
			var clientResponse map[string]any
			if json.Unmarshal(scanner.Bytes(), &clientResponse) != nil || clientResponse["error"] == nil {
				os.Exit(4)
			}
			_ = encoder.Encode(map[string]any{
				"jsonrpc": "2.0",
				"id":      id,
				"result": map[string]any{
					"content":           []any{map[string]any{"type": "text", "text": "done"}},
					"structuredContent": map[string]any{"ok": true},
				},
			})
		}
	}
}

func TestStdioMCPTransportHandlesNotificationsServerRequestsAndPagination(t *testing.T) {
	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("GO_WANT_MCP_STDIO_HELPER", "1")
	transport := newStdioMCPTransport(MCPPluginSpec{
		Name:      "test",
		Transport: "stdio",
		Command:   executable,
		Args:      []string{"-test.run=^TestMCPStdioHelper$"},
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := transport.Initialize(ctx); err != nil {
		t.Fatal(err)
	}
	tools, err := transport.ListTools(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if names := []string{tools[0].Name, tools[1].Name}; !reflect.DeepEqual(names, []string{"one", "two"}) {
		t.Fatalf("tools=%v", names)
	}
	result, err := transport.CallTool(ctx, "one", map[string]any{"q": "x"})
	if err != nil {
		t.Fatal(err)
	}
	if result.IsError || len(result.Content) != 1 {
		t.Fatalf("result=%+v", result)
	}
	if err := transport.Close(); err != nil {
		t.Fatal(err)
	}
}
