package runtimecfg

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestVertexModelURLDefaultsGlobalLocation(t *testing.T) {
	got := VertexModelURL("https://example.test/v1", "project-x", "", "gemini-3-flash-preview")
	want := "https://example.test/v1/projects/project-x/locations/global/publishers/google/models/gemini-3-flash-preview:generateContent"
	if got != want {
		t.Fatalf("url=%q want=%q", got, want)
	}
}

func TestProbeVertexAutoDoesNotRequireCredentials(t *testing.T) {
	result := ProbeVertexModel(context.Background(), http.DefaultClient, "", VertexAuditCredentials{}, "auto")
	if result.Status != "ok" || result.Reason != "runtime_default" || result.HTTPStatus == nil || *result.HTTPStatus != 200 {
		t.Fatalf("result=%+v", result)
	}
}

func TestProbeVertexMissingCredentials(t *testing.T) {
	result := ProbeVertexModel(context.Background(), http.DefaultClient, "", VertexAuditCredentials{}, "gemini-3-flash-preview")
	if result.Status != "unknown" || result.Reason != "vertex_credentials_missing" {
		t.Fatalf("result=%+v", result)
	}
}

func TestProbeVertexModelSendsExpectedRequest(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer token-x" {
			t.Errorf("authorization=%q", r.Header.Get("Authorization"))
		}
		if !strings.Contains(r.URL.Path, "/projects/project-x/locations/asia-southeast1/publishers/google/models/gemini-3-flash-preview:generateContent") {
			t.Errorf("path=%q", r.URL.Path)
		}
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("decode payload: %v", err)
		}
		if _, ok := payload["contents"]; !ok {
			t.Errorf("payload=%v", payload)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}`))
	}))
	defer server.Close()

	result := ProbeVertexModel(
		context.Background(),
		server.Client(),
		server.URL,
		VertexAuditCredentials{Token: "token-x", Project: "project-x", Location: "asia-southeast1"},
		"gemini-3-flash-preview",
	)
	if result.Status != "ok" || result.Reason != "live_probe" {
		t.Fatalf("result=%+v", result)
	}
}

func TestAuditVertexProviderReportsCredentialAndModelStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "gemini-3.1-flash-lite") {
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"error":"model not found"}`))
			return
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()

	state := BlankCapabilityState()
	report := AuditVertexProvider(
		context.Background(),
		server.Client(),
		server.URL,
		VertexAuditCredentials{Token: "token-x", Project: "project-x"},
		&state,
		500,
	)
	if report.Key.Status != "ok" || report.Key.Reason != "service_account_valid" {
		t.Fatalf("key=%+v", report.Key)
	}
	if got := report.Models["auto"].Status; got != "ok" {
		t.Fatalf("auto=%+v", report.Models["auto"])
	}
	if got := report.Models["gemini-3.1-flash-lite"]; got.Status != "dead" || got.Reason != "model_not_available" {
		t.Fatalf("lite=%+v", got)
	}
	if got := state.CapabilityModelStatus("vertex", "gemini-3.1-flash-lite"); got != "dead" {
		t.Fatalf("stored=%q", got)
	}
}
