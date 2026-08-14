package runtimecfg

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAuditOpenAIProviderMissingKeyMarksModelsUnknown(t *testing.T) {
	state := BlankCapabilityState()
	report := AuditOpenAIProvider(
		context.Background(),
		http.DefaultClient,
		"cerebras",
		"",
		OpenAIProviderEndpoints{},
		&state,
		100,
	)
	if report.Key.Status != "missing" || report.Key.Reason != "key_missing" {
		t.Fatalf("key=%+v", report.Key)
	}
	for _, choice := range CandidateModelChoices["cerebras"] {
		result := report.Models[choice.Model]
		if result.Status != "unknown" || result.Reason != "key_missing" {
			t.Fatalf("model=%s result=%+v", choice.Model, result)
		}
		if got := state.CapabilityModelStatus("cerebras", choice.Model); got != "unknown" {
			t.Fatalf("stored status=%q", got)
		}
	}
}

func TestAuditOpenAIProviderUsesDiscoveryAndProbe(t *testing.T) {
	var probedModel string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer secret" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		switch r.URL.Path {
		case "/key":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"ok":true}`))
		case "/models":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"data":[{"id":"gpt-oss-120b"}]}`))
		case "/chat":
			var payload map[string]any
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Errorf("decode payload: %v", err)
			}
			probedModel, _ = payload["model"].(string)
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"OK"}}]}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	state := BlankCapabilityState()
	report := AuditOpenAIProvider(
		context.Background(),
		server.Client(),
		"cerebras",
		"secret",
		OpenAIProviderEndpoints{
			Key:    server.URL + "/key",
			Models: server.URL + "/models",
			Chat:   server.URL + "/chat",
		},
		&state,
		200,
	)
	if report.Key.Status != "ok" {
		t.Fatalf("key=%+v", report.Key)
	}
	if probedModel != "gpt-oss-120b" {
		t.Fatalf("probed=%q", probedModel)
	}
	if got := report.Models["gpt-oss-120b"].Status; got != "ok" {
		t.Fatalf("listed model=%+v", report.Models["gpt-oss-120b"])
	}
	if got := report.Models["zai-glm-4.7"]; got.Status != "dead" || got.Reason != "model_not_listed" {
		t.Fatalf("unlisted model=%+v", got)
	}
	if discovered := state.Discovered["cerebras"]; len(discovered) != 1 || discovered[0] != "gpt-oss-120b" {
		t.Fatalf("discovered=%v", discovered)
	}
}

func TestProbeOpenAIModelClassifiesTerminalModelError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"error":"invalid model does not exist"}`))
	}))
	defer server.Close()

	result := ProbeOpenAIModel(
		context.Background(),
		server.Client(),
		"groq",
		server.URL,
		"secret",
		"openai/gpt-oss-120b",
	)
	if result.Status != "dead" || result.Reason != "model_not_available" {
		t.Fatalf("result=%+v", result)
	}
}

func TestAuditOpenAIProvidersUpdatesAuditTimeAndSkipsUnknownProvider(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "models") {
			_, _ = w.Write([]byte(`{"data":[]}`))
			return
		}
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()

	state := BlankCapabilityState()
	report := AuditOpenAIProviders(
		context.Background(),
		server.Client(),
		[]string{"cerebras", "unknown"},
		map[string]string{"cerebras": "secret"},
		map[string]OpenAIProviderEndpoints{
			"cerebras": {
				Key:    server.URL + "/key",
				Models: server.URL + "/models",
				Chat:   server.URL + "/chat",
			},
		},
		&state,
		321,
	)
	if _, ok := report["unknown"]; ok {
		t.Fatalf("unknown provider included: %v", report)
	}
	if state.LastAuditAt != 321 {
		t.Fatalf("last audit=%d", state.LastAuditAt)
	}
}

func TestDiscoverOpenAIModelsRejectsInvalidJSON(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("not-json"))
	}))
	defer server.Close()

	result, models := DiscoverOpenAIModels(
		context.Background(),
		server.Client(),
		"groq",
		server.URL,
		"secret",
		nil,
	)
	if result.Status != "unknown" || result.Reason != "JSONDecodeError" || len(models) != 0 {
		t.Fatalf("result=%+v models=%v", result, models)
	}
}
