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
	if declarations := runtime.Registry.Declarations("chat", false); len(declarations) != 12 {
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
	if declarations := runtime.Registry.Declarations("chat", true); len(declarations) != 18 {
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
