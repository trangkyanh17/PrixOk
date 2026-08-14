package runtimecfg

import (
	"context"
	"testing"
)

func TestNewConfiguredBuiltinRuntimeWithoutCredentials(t *testing.T) {
	runtime, err := NewConfiguredBuiltinRuntime(BuiltinRuntimeConfig{
		Values: map[string]string{
			"GOOGLE_API_KEY": "public-key",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if runtime.Registry == nil {
		t.Fatal("registry is nil")
	}
	if runtime.Credentials != nil {
		t.Fatalf("credentials=%T", runtime.Credentials)
	}
	if runtime.Workspace != nil {
		t.Fatalf("workspace=%T", runtime.Workspace)
	}
	if runtime.Audio.Credentials != nil {
		t.Fatal("audio credentials should be nil")
	}
	if runtime.MCP != nil {
		t.Fatalf("MCP=%T", runtime.MCP)
	}
	if declarations := runtime.Registry.Declarations("chat", false); len(declarations) != 15 {
		t.Fatalf("public declarations=%v", declarations)
	}

	capabilities := runtime.Registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"google_capabilities",
		map[string]any{},
		false,
	).(map[string]any)
	configured := capabilities["configured"].(map[string]any)
	if configured["books"] != true ||
		configured["places_routes_geocoding"] != true ||
		configured["translation_speech_tts_vision"] != false {
		t.Fatalf("configured=%v", configured)
	}
}

func TestNewConfiguredBuiltinRuntimeWiresOAuthWorkspace(t *testing.T) {
	runtime, err := NewConfiguredBuiltinRuntime(BuiltinRuntimeConfig{
		Values: map[string]string{
			"GOOGLE_OAUTH_CLIENT_ID":     "client",
			"GOOGLE_OAUTH_CLIENT_SECRET": "secret",
			"GOOGLE_OAUTH_REFRESH_TOKEN": "refresh",
		},
		OAuthTokenURL: "https://oauth.example/token",
	})
	if err != nil {
		t.Fatal(err)
	}
	provider, ok := runtime.Workspace.(*OAuthRefreshTokenProvider)
	if !ok {
		t.Fatalf("workspace=%T", runtime.Workspace)
	}
	if provider.TokenURL != "https://oauth.example/token" {
		t.Fatalf("token URL=%q", provider.TokenURL)
	}
	if declarations := runtime.Registry.Declarations("chat", true); len(declarations) != 21 {
		t.Fatalf("owner declarations=%v", declarations)
	}
}

func TestNewConfiguredBuiltinRuntimeWiresServiceAccountReferences(t *testing.T) {
	runtime, err := NewConfiguredBuiltinRuntime(BuiltinRuntimeConfig{
		Values: map[string]string{
			"GOOGLE_APPLICATION_CREDENTIALS":   "/tmp/cloud.json",
			"GOOGLE_WORKSPACE_SERVICE_ACCOUNT": "true",
			"GOOGLE_WORKSPACE_SUBJECT":         "owner@example.com",
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	if runtime.Credentials == nil || runtime.Credentials.Path != "/tmp/cloud.json" {
		t.Fatalf("credentials=%+v", runtime.Credentials)
	}
	workspace, ok := runtime.Workspace.(*WorkspaceServiceAccountTokenProvider)
	if !ok {
		t.Fatalf("workspace=%T", runtime.Workspace)
	}
	if workspace.Path != "/tmp/cloud.json" || workspace.Subject != "owner@example.com" {
		t.Fatalf("workspace=%+v", workspace)
	}
	if runtime.Audio.Credentials != runtime.Credentials {
		t.Fatal("audio does not share cloud credentials")
	}
}

func TestNewConfiguredBuiltinRuntimeWiresDeltaForcePaths(t *testing.T) {
	called := false
	runtime, err := NewConfiguredBuiltinRuntime(BuiltinRuntimeConfig{
		DeltaForceBinary: "/tmp/atri-native",
		DeltaForceDB:     "/tmp/delta.sqlite3",
		DeltaForceInvoker: func(
			_ context.Context,
			binaryPath string,
			command string,
			dbPath string,
			_ []byte,
		) ([]byte, error) {
			called = true
			if binaryPath != "/tmp/atri-native" || dbPath != "/tmp/delta.sqlite3" || command != "delta-search" {
				t.Fatalf("binary=%q db=%q command=%q", binaryPath, dbPath, command)
			}
			return []byte(`{"ok":true,"region":"cn"}`), nil
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	result := runtime.Registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"search_delta_force_cn",
		map[string]any{"query": "M4"},
		false,
	).(map[string]any)
	if result["ok"] != true || !called {
		t.Fatalf("result=%v called=%v", result, called)
	}
}

func TestNewConfiguredBuiltinRuntimeWiresMCPBackend(t *testing.T) {
	backend := testMCPBackend()
	runtime, err := NewConfiguredBuiltinRuntime(BuiltinRuntimeConfig{
		MCPBackend: backend,
	})
	if err != nil {
		t.Fatal(err)
	}
	if runtime.MCP == nil || runtime.MCP.Backend != backend {
		t.Fatalf("MCP=%+v", runtime.MCP)
	}
	if !runtime.Registry.Has("code_plugin_search") || !runtime.Registry.Has("code_plugin_call") {
		t.Fatal("MCP registry tools are missing")
	}
	if declarations := runtime.Registry.Declarations("chat", false); len(declarations) != 15 {
		t.Fatalf("MCP tools leaked into chat declarations: %v", declarations)
	}

	result := runtime.Registry.Execute(
		context.Background(),
		ToolContext{Mode: "code"},
		"code_plugin_search",
		map[string]any{"query": "github repository file", "limit": 5},
		false,
	).(map[string]any)
	if result["ok"] != true {
		t.Fatalf("MCP search=%v", result)
	}
}
