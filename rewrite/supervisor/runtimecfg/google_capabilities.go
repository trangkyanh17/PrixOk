package runtimecfg

import (
	"context"
	"fmt"
	"strings"
)

type GoogleCapabilitiesRuntime struct {
	Values map[string]string
}

func GoogleCapabilitiesDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_capabilities",
		"description": "Kiểm tra nhóm Google tool nào của Atri đã có credential/config.",
		"parameters": map[string]any{
			"type":       "object",
			"properties": map[string]any{},
			"required":   []any{},
		},
	}
}

func (runtime GoogleCapabilitiesRuntime) setting(names ...string) string {
	return (GooglePublicToolRuntime{Values: runtime.Values}).setting(names...)
}

func googleBoolSetting(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func (runtime GoogleCapabilitiesRuntime) Capabilities() map[string]any {
	project := runtime.setting("GOOGLE_CLOUD_PROJECT", "VERTEX_PROJECT_ID")
	credentialPath := runtime.setting("GOOGLE_APPLICATION_CREDENTIALS")
	googleKey := runtime.setting("GOOGLE_API_KEY")
	maps := runtime.setting("GOOGLE_MAPS_API_KEY") != "" || googleKey != ""
	cloud := project != "" && credentialPath != ""

	workspaceOAuth := runtime.setting("GOOGLE_OAUTH_CLIENT_ID") != "" &&
		runtime.setting("GOOGLE_OAUTH_CLIENT_SECRET") != "" &&
		runtime.setting("GOOGLE_OAUTH_REFRESH_TOKEN") != ""
	workspaceOAuth = workspaceOAuth ||
		googleBoolSetting(runtime.setting("GOOGLE_WORKSPACE_SERVICE_ACCOUNT"))

	return googleToolOK(map[string]any{
		"configured": map[string]any{
			"vertex_web_search":             project != "",
			"youtube":                       runtime.setting("YOUTUBE_API_KEY") != "" || googleKey != "",
			"safe_browsing":                 runtime.setting("SAFE_BROWSING_API_KEY") != "" || googleKey != "",
			"places_routes_geocoding":       maps,
			"translation_speech_tts_vision": cloud,
			"document_ai": cloud &&
				runtime.setting("GOOGLE_DOCUMENT_AI_PROCESSOR_ID") != "",
			"gmail_drive_calendar_sheets": workspaceOAuth,
			"books":                       true,
		},
	})
}

func (runtime GoogleCapabilitiesRuntime) RegisteredTool() RegisteredTool {
	return RegisteredTool{
		Name:        "google_capabilities",
		Declaration: GoogleCapabilitiesDeclaration(),
		Privacy:     ToolPrivacyPublic,
		Executor: func(context.Context, ToolContext, map[string]any) (any, error) {
			return runtime.Capabilities(), nil
		},
	}
}

func RegisterGoogleCapabilitiesTool(registry *ToolRegistry, runtime GoogleCapabilitiesRuntime) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	return registry.Register(runtime.RegisteredTool())
}
