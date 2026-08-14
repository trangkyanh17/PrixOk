package runtimecfg

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func testVertexService(
	t *testing.T,
	vertexServer *httptest.Server,
) *VertexServiceRuntime {
	t.Helper()
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	tokenServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"access_token":"vertex-token","expires_in":3600}`))
	}))
	t.Cleanup(tokenServer.Close)
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, tokenServer.URL)
	service := NewVertexServiceRuntime(path, "global", "gemini-3-flash-preview")
	service.APIBaseURL = vertexServer.URL
	service.Credentials.Client = tokenServer.Client()
	return service
}

func TestAtriOrchestratorDirectTextFlow(t *testing.T) {
	var received map[string]any
	vertexServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer vertex-token" {
			t.Errorf("authorization=%q", r.Header.Get("Authorization"))
		}
		if err := json.NewDecoder(r.Body).Decode(&received); err != nil {
			t.Errorf("decode=%v", err)
		}
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"final answer"}]}}]}`))
	}))
	defer vertexServer.Close()

	orchestrator := AtriOrchestrator{
		VertexService: testVertexService(t, vertexServer),
		VertexClient:  vertexServer.Client(),
		VertexSleep:   noVertexSleep,
	}
	result, err := orchestrator.Run(context.Background(), OrchestratorRequest{
		Mode:              "chat",
		PublicText:        "hello",
		SystemInstruction: "SYSTEM",
		MemoryContext:     "MEMORY",
		GenerationConfig: map[string]any{
			"temperature": 0.2,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Text != "final answer" || len(result.Chunks) != 1 || result.Chunks[0] != "final answer" {
		t.Fatalf("result=%+v", result)
	}
	if result.WorkerUsed {
		t.Fatalf("unexpected worker: %+v", result)
	}
	system := received["systemInstruction"].(map[string]any)["parts"].([]any)[0].(map[string]any)["text"].(string)
	if !strings.Contains(system, "SYSTEM") || !strings.Contains(system, "MEMORY") {
		t.Fatalf("system=%q", system)
	}
	contents := received["contents"].([]any)
	if len(contents) != 1 || contents[0].(map[string]any)["role"] != "user" {
		t.Fatalf("contents=%v", contents)
	}
}

func TestAtriOrchestratorUsesRegistryToolsEndToEnd(t *testing.T) {
	requests := 0
	var secondPayload map[string]any
	vertexServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode=%v", err)
		}
		if requests == 1 {
			tools := payload["tools"].([]any)
			declarations := tools[0].(map[string]any)["functionDeclarations"].([]any)
			if len(declarations) != 1 || declarations[0].(map[string]any)["name"] != "echo" {
				t.Errorf("declarations=%v", declarations)
			}
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"echo","args":{"value":"hello"}}}]}}]}`))
			return
		}
		secondPayload = payload
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"tool final"}]}}]}`))
	}))
	defer vertexServer.Close()

	registry := NewToolRegistry()
	registry.MustRegister(RegisteredTool{
		Name:        "echo",
		Declaration: testToolDeclaration("echo"),
		Privacy:     ToolPrivacyPublic,
		Modes:       []string{"chat"},
		Executor: func(ctx context.Context, toolContext ToolContext, arguments map[string]any) (any, error) {
			return map[string]any{
				"ok":    true,
				"value": arguments["value"],
				"user":  toolContext.UserID,
			}, nil
		},
	})

	orchestrator := AtriOrchestrator{
		VertexService: testVertexService(t, vertexServer),
		Registry:      registry,
		VertexClient:  vertexServer.Client(),
		VertexSleep:   noVertexSleep,
	}
	result, err := orchestrator.Run(context.Background(), OrchestratorRequest{
		Mode:       "chat",
		PublicText: "use echo",
		ToolContext: ToolContext{
			UserID: 77,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Text != "tool final" || requests != 2 {
		t.Fatalf("result=%+v requests=%d", result, requests)
	}
	contents := secondPayload["contents"].([]any)
	last := contents[len(contents)-1].(map[string]any)
	response := last["parts"].([]any)[0].(map[string]any)["functionResponse"].(map[string]any)
	resultValue := response["response"].(map[string]any)["result"].(map[string]any)
	if resultValue["ok"] != true || resultValue["value"] != "hello" || resultValue["user"] != float64(77) && resultValue["user"] != int64(77) {
		t.Fatalf("tool result=%v", resultValue)
	}
}

func TestAtriOrchestratorWorkerVerifyThenSupervisorFinal(t *testing.T) {
	freeServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"worker draft"}}]}`))
	}))
	defer freeServer.Close()
	overrideFreeProviderURL(t, "groq_gptoss", freeServer.URL)

	vertexRequests := 0
	var finalPayload map[string]any
	vertexServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		vertexRequests++
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode=%v", err)
		}
		if vertexRequests == 1 {
			_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"{\"verdict\":\"PASS\",\"feedback\":\"good\"}"}]}}]}`))
			return
		}
		finalPayload = payload
		_, _ = w.Write([]byte(`{"candidates":[{"finishReason":"STOP","content":{"role":"model","parts":[{"text":"supervisor final"}]}}]}`))
	}))
	defer vertexServer.Close()

	freePool := &FreePoolRuntime{
		Values: map[string]string{
			"GROQ_API_KEY": "free-key",
		},
		Control: DefaultControlState(),
		Router:  NewSmartRouterState(),
	}
	orchestrator := AtriOrchestrator{
		FreePool:      freePool,
		VertexService: testVertexService(t, vertexServer),
		VertexClient:  vertexServer.Client(),
		VertexSleep:   noVertexSleep,
	}
	result, err := orchestrator.Run(context.Background(), OrchestratorRequest{
		Mode:              "chat",
		PublicText:        "debug python function này",
		SystemInstruction: "SYSTEM",
		MemoryContext:     "MEMORY",
		ThinkingLevel:     "medium",
	})
	if err != nil {
		t.Fatal(err)
	}
	if !result.WorkerUsed || result.WorkerTaskType != "coding" || result.WorkerProvider != "groq" || result.VerifierVerdict != "PASS" || result.WorkerRetried {
		t.Fatalf("result=%+v", result)
	}
	if result.Text != "supervisor final" || vertexRequests != 2 {
		t.Fatalf("result=%+v vertexRequests=%d", result, vertexRequests)
	}
	system := finalPayload["systemInstruction"].(map[string]any)["parts"].([]any)[0].(map[string]any)["text"].(string)
	for _, expected := range []string{"SYSTEM", "MEMORY", "ATRI INTERNAL SUPERVISOR CONTEXT V25", "worker draft", "verdict=PASS"} {
		if !strings.Contains(system, expected) {
			t.Fatalf("system missing %q: %q", expected, system)
		}
	}
}

func TestAtriOrchestratorRequiresVertexService(t *testing.T) {
	orchestrator := AtriOrchestrator{}
	_, err := orchestrator.Run(context.Background(), OrchestratorRequest{PublicText: "hello"})
	if err == nil || !strings.Contains(err.Error(), "vertex service runtime") {
		t.Fatalf("err=%v", err)
	}
}
