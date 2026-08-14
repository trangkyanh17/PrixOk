package runtimecfg

import (
	"context"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
)

func TestAuditCapabilitiesPersistsRequestedProviderState(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/key":
			_, _ = w.Write([]byte(`{"ok":true}`))
		case "/models":
			_, _ = w.Write([]byte(`{"data":[{"id":"gpt-oss-120b"},{"id":"zai-glm-4.7"}]}`))
		case "/chat":
			_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"OK"}}]}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	path := filepath.Join(t.TempDir(), "capabilities.json")
	state := BlankCapabilityState()
	report, err := AuditCapabilities(
		context.Background(),
		server.Client(),
		ProviderAuditOptions{
			Providers: []string{"cerebras"},
			Keys:      map[string]string{"cerebras": "secret"},
			OpenAIEndpoints: map[string]OpenAIProviderEndpoints{
				"cerebras": {
					Key:    server.URL + "/key",
					Models: server.URL + "/models",
					Chat:   server.URL + "/chat",
				},
			},
			StatePath: path,
			Now:       700,
		},
		&state,
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(report) != 1 || report["cerebras"].Key.Status != "ok" {
		t.Fatalf("report=%+v", report)
	}
	if state.LastAuditAt != 700 {
		t.Fatalf("last audit=%d", state.LastAuditAt)
	}
	loaded := LoadCapabilityState(path)
	if loaded.LastAuditAt != 700 || loaded.CapabilityModelStatus("cerebras", "gpt-oss-120b") != "ok" {
		t.Fatalf("loaded=%+v", loaded)
	}
}

func TestAuditCapabilitiesCanCombineOpenAIAndVertex(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/key" {
			_, _ = w.Write([]byte(`{"ok":true}`))
			return
		}
		if r.URL.Path == "/models" {
			_, _ = w.Write([]byte(`{"data":[]}`))
			return
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()

	state := BlankCapabilityState()
	report, err := AuditCapabilities(
		context.Background(),
		server.Client(),
		ProviderAuditOptions{
			Providers: []string{"groq", "vertex", "groq"},
			Keys:      map[string]string{"groq": "secret"},
			OpenAIEndpoints: map[string]OpenAIProviderEndpoints{
				"groq": {
					Key:    server.URL + "/key",
					Models: server.URL + "/models",
					Chat:   server.URL + "/chat",
				},
			},
			VertexBaseURL: server.URL,
			VertexCredentials: VertexAuditCredentials{
				Token:   "token",
				Project: "project",
			},
			Now: 800,
		},
		&state,
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(report) != 2 {
		t.Fatalf("report keys=%v", report)
	}
	if report["vertex"].Key.Status != "ok" {
		t.Fatalf("vertex=%+v", report["vertex"])
	}
	if state.LastAuditAt != 800 {
		t.Fatalf("last audit=%d", state.LastAuditAt)
	}
}

func TestRequestedAuditProvidersDefaultsAndDeduplicates(t *testing.T) {
	defaults := requestedAuditProviders(nil)
	if len(defaults) != 4 || defaults[0] != "cerebras" || defaults[3] != "vertex" {
		t.Fatalf("defaults=%v", defaults)
	}
	custom := requestedAuditProviders([]string{" Groq ", "groq", "vertex", ""})
	if len(custom) != 2 || custom[0] != "groq" || custom[1] != "vertex" {
		t.Fatalf("custom=%v", custom)
	}
}
