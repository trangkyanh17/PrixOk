package runtimecfg

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"strings"
	"testing"
	"time"
)

func TestRegistryToolRuntimeWiresRegistryContextAndOptions(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, "https://oauth.example/token")
	service := NewVertexServiceRuntime(path, "global", "gemini-3-flash-preview")
	service.APIBaseURL = "https://vertex.example/v1"

	registry := NewToolRegistry()
	registry.MustRegister(RegisteredTool{
		Name:        "echo",
		Declaration: testToolDeclaration("echo"),
		Privacy:     ToolPrivacyPublic,
		Modes:       []string{"code"},
		Executor: func(ctx context.Context, toolContext ToolContext, arguments map[string]any) (any, error) {
			return map[string]any{
				"ok":     true,
				"user":   toolContext.UserID,
				"chat":   toolContext.ChatID,
				"thread": toolContext.ThreadID,
				"mode":   toolContext.Mode,
				"value":  arguments["value"],
			}, nil
		},
	})

	progressCalled := false
	runtime, err := service.RegistryToolRuntime(registry, VertexRegistryRuntimeOptions{
		Mode: " CODE ",
		ToolContext: ToolContext{
			UserID:   7,
			ChatID:   8,
			ThreadID: 9,
		},
		ProgressCallback: func(stage int, text string) error {
			progressCalled = true
			return nil
		},
		CodeToolConcurrency:   5,
		CodeToolTimeout:       42,
		MaxContinuationRounds: 4,
		MaxEmptyTextRetries:   2,
		ForceGitHubMCP:        true,
		DirectPluginName:      " github ",
	})
	if err != nil {
		t.Fatal(err)
	}
	if runtime.Mode != "code" || runtime.CodeToolConcurrency != 5 || runtime.CodeToolTimeout != 42*time.Second {
		t.Fatalf("runtime=%+v", runtime)
	}
	if runtime.MaxContinuationRounds != 4 || runtime.MaxEmptyTextRetries != 2 || !runtime.ForceGitHubMCP || runtime.DirectPluginName != "github" {
		t.Fatalf("runtime=%+v", runtime)
	}
	if runtime.ProgressCallback == nil {
		t.Fatal("progress callback not wired")
	}
	_ = runtime.ProgressCallback(1, "x")
	if !progressCalled {
		t.Fatal("progress callback not invoked")
	}

	result, err := runtime.ToolExecutor(context.Background(), "echo", map[string]any{"value": "hello"})
	if err != nil {
		t.Fatal(err)
	}
	value := result.(map[string]any)
	if value["ok"] != true || value["user"] != int64(7) || value["chat"] != int64(8) || value["thread"] != int64(9) || value["mode"] != "code" || value["value"] != "hello" {
		t.Fatalf("result=%v", value)
	}
}

func TestRegistryToolRuntimeEnforcesPrivateToolGate(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, "https://oauth.example/token")
	service := NewVertexServiceRuntime(path, "global", "gemini-3-flash-preview")
	service.APIBaseURL = "https://vertex.example/v1"

	registry := NewToolRegistry()
	registry.MustRegister(RegisteredTool{
		Name:        "private_tool",
		Declaration: testToolDeclaration("private_tool"),
		Privacy:     ToolPrivacyPrivate,
		Modes:       []string{"chat"},
		Executor: func(context.Context, ToolContext, map[string]any) (any, error) {
			return map[string]any{"ok": true}, nil
		},
	})

	runtime, err := service.RegistryToolRuntime(registry, VertexRegistryRuntimeOptions{
		Mode:              "chat",
		AllowPrivateTools: false,
	})
	if err != nil {
		t.Fatal(err)
	}
	result, err := runtime.ToolExecutor(context.Background(), "private_tool", nil)
	if err != nil {
		t.Fatal(err)
	}
	value := result.(map[string]any)
	if value["ok"] != false || !strings.Contains(value["error"].(string), "private runtime context") {
		t.Fatalf("result=%v", value)
	}
}

func TestRegistryToolRuntimeRequiresRegistry(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, "https://oauth.example/token")
	service := NewVertexServiceRuntime(path, "global", "gemini-3-flash-preview")
	if _, err := service.RegistryToolRuntime(nil, VertexRegistryRuntimeOptions{}); err == nil || !strings.Contains(err.Error(), "tool registry") {
		t.Fatalf("err=%v", err)
	}
}
