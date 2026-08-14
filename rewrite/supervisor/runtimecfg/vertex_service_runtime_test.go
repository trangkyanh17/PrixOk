package runtimecfg

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestVertexServiceRuntimeBuildsGenerationURL(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	tokenServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"access_token":"token","expires_in":3600}`))
	}))
	defer tokenServer.Close()

	path := writeServiceAccountFile(t, t.TempDir(), privateKey, tokenServer.URL)
	runtime := NewVertexServiceRuntime(path, "asia-southeast1", "gemini-3-flash-preview")
	runtime.APIBaseURL = "https://vertex.example/v1"
	url, err := runtime.GenerationURL()
	if err != nil {
		t.Fatal(err)
	}
	want := "https://vertex.example/v1/projects/project-x/locations/asia-southeast1/publishers/google/models/gemini-3-flash-preview:generateContent"
	if url != want {
		t.Fatalf("url=%q want=%q", url, want)
	}
}

func TestVertexServiceRuntimeDefaultsLocationAndRejectsAutoModel(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, "https://oauth.example/token")
	runtime := NewVertexServiceRuntime(path, "", "auto")
	if runtime.normalizedLocation() != "global" {
		t.Fatalf("location=%q", runtime.normalizedLocation())
	}
	if _, err := runtime.GenerationURL(); err == nil || !strings.Contains(err.Error(), "model must be resolved") {
		t.Fatalf("err=%v", err)
	}
}

func TestVertexServiceRuntimeTokenProviderUsesCredentialCache(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	requests := 0
	tokenServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		_, _ = w.Write([]byte(`{"access_token":"token-x","expires_in":3600}`))
	}))
	defer tokenServer.Close()

	path := writeServiceAccountFile(t, t.TempDir(), privateKey, tokenServer.URL)
	runtime := NewVertexServiceRuntime(path, "global", "gemini-3-flash-preview")
	runtime.Credentials.Client = tokenServer.Client()
	provider := runtime.TokenProvider()
	first, err := provider(context.Background(), false)
	if err != nil || first != "token-x" {
		t.Fatalf("first=%q err=%v", first, err)
	}
	second, err := provider(context.Background(), false)
	if err != nil || second != "token-x" {
		t.Fatalf("second=%q err=%v", second, err)
	}
	if requests != 1 {
		t.Fatalf("requests=%d", requests)
	}
}

func TestVertexServiceRuntimeCreatesTextAndToolRuntimes(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, "https://oauth.example/token")
	service := NewVertexServiceRuntime(path, "global", "gemini-3-flash-preview")
	service.APIBaseURL = "https://vertex.example/v1"

	textRuntime, err := service.TextRuntime(nil, noVertexSleep, 3, 2)
	if err != nil {
		t.Fatal(err)
	}
	if textRuntime.URL == "" || textRuntime.TokenProvider == nil || textRuntime.MaxContinuationRounds != 3 || textRuntime.MaxEmptyTextRetries != 2 {
		t.Fatalf("text runtime=%+v", textRuntime)
	}

	executor := func(context.Context, string, map[string]any) (any, error) {
		return map[string]any{"ok": true}, nil
	}
	toolRuntime, err := service.ToolRuntime(nil, noVertexSleep, "code", executor)
	if err != nil {
		t.Fatal(err)
	}
	if toolRuntime.URL == "" || toolRuntime.TokenProvider == nil || toolRuntime.Mode != "code" || toolRuntime.ToolExecutor == nil {
		t.Fatalf("tool runtime=%+v", toolRuntime)
	}
}

func TestVertexServiceRuntimeNilCredentialsFailCleanly(t *testing.T) {
	var service *VertexServiceRuntime
	if _, err := service.GenerationURL(); err == nil {
		t.Fatal("nil service should fail")
	}
	service = &VertexServiceRuntime{}
	if _, err := service.TokenProvider()(context.Background(), false); err == nil {
		t.Fatal("nil credentials should fail")
	}
}
