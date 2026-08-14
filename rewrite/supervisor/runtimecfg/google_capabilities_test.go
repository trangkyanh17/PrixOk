package runtimecfg

import (
	"context"
	"testing"
)

func TestGoogleCapabilitiesParity(t *testing.T) {
	runtime := GoogleCapabilitiesRuntime{
		Values: map[string]string{
			"VERTEX_PROJECT_ID":                  "project-x",
			"GOOGLE_APPLICATION_CREDENTIALS":     "/tmp/service-account.json",
			"YOUTUBE_API_KEY":                    "youtube-key",
			"SAFE_BROWSING_API_KEY":              "safe-key",
			"GOOGLE_MAPS_API_KEY":                "maps-key",
			"GOOGLE_DOCUMENT_AI_PROCESSOR_ID":     "processor",
			"GOOGLE_OAUTH_CLIENT_ID":              "client",
			"GOOGLE_OAUTH_CLIENT_SECRET":          "secret",
			"GOOGLE_OAUTH_REFRESH_TOKEN":          "refresh",
			"GOOGLE_WORKSPACE_SERVICE_ACCOUNT":    "0",
		},
	}
	result := runtime.Capabilities()
	if result["ok"] != true {
		t.Fatalf("result=%v", result)
	}
	configured := result["configured"].(map[string]any)
	for _, name := range []string{
		"vertex_web_search",
		"youtube",
		"safe_browsing",
		"places_routes_geocoding",
		"translation_speech_tts_vision",
		"document_ai",
		"gmail_drive_calendar_sheets",
		"books",
	} {
		if configured[name] != true {
			t.Fatalf("%s=%v configured=%v", name, configured[name], configured)
		}
	}
}

func TestGoogleCapabilitiesWorkspaceServiceAccountFallback(t *testing.T) {
	runtime := GoogleCapabilitiesRuntime{
		Values: map[string]string{
			"GOOGLE_WORKSPACE_SERVICE_ACCOUNT": "true",
		},
	}
	configured := runtime.Capabilities()["configured"].(map[string]any)
	if configured["gmail_drive_calendar_sheets"] != true {
		t.Fatalf("configured=%v", configured)
	}
	if configured["books"] != true {
		t.Fatalf("books=%v", configured["books"])
	}
	if configured["translation_speech_tts_vision"] != false {
		t.Fatalf("cloud=%v", configured["translation_speech_tts_vision"])
	}
}

func TestRegisterGoogleCapabilitiesTool(t *testing.T) {
	registry := NewToolRegistry()
	if err := RegisterGoogleCapabilitiesTool(registry, GoogleCapabilitiesRuntime{}); err != nil {
		t.Fatal(err)
	}
	if !registry.Has("google_capabilities") {
		t.Fatal("capabilities tool not registered")
	}
	result := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"google_capabilities",
		map[string]any{},
		false,
	).(map[string]any)
	if result["ok"] != true {
		t.Fatalf("result=%v", result)
	}
}
