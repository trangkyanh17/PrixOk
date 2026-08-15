package main

import "testing"

func TestChooseAtriModeParity(t *testing.T) {
	tests := []struct {
		text string
		want string
	}{
		{"hello Atri", "chat"},
		{"thời tiết Hà Nội hôm nay", "tools"},
		{"tho\u031Bi tie\u0302\u0301t Ha\u0300 No\u0323\u0302i", "tools"},
		{"lịch của tôi hôm nay có gì", "tools"},
		{"phiên bản Ubuntu mới nhất", "web"},
		{"xem https://example.com này", "web"},
		{"sửa code python giúp tao", "code"},
		{"tìm trên GitHub repo PrixOk", "code"},
		{"Ubuntu còn hỗ trợ tới năm nào", "web"},
	}
	for _, tt := range tests {
		if got := chooseAtriModeParity(tt.text); got != tt.want {
			t.Fatalf("text=%q got=%s want=%s", tt.text, got, tt.want)
		}
	}
	if !isExplicitGitHubLookupParity("tìm trên GitHub repo PrixOk") {
		t.Fatal("explicit GitHub lookup should be detected")
	}
	if got := applyAttachmentRouteParity("chat", "code"); got != "code" {
		t.Fatalf("attachment override got=%s", got)
	}
}

func TestAtriParityRouteMatchAndMismatch(t *testing.T) {
	engine := newAtriParityEngine()
	match, reason, err := engine.evaluate(atriParityEvent{
		Version:        atriParitySchemaVersion,
		Stage:          "route",
		RouteText:      "tìm trên GitHub repo PrixOk",
		ActualMode:     "code",
		ForceGitHubMCP: true,
	})
	if err != nil || !match || reason != "ok" {
		t.Fatalf("match=%v reason=%s err=%v", match, reason, err)
	}
	match, reason, err = engine.evaluate(atriParityEvent{
		Version:    atriParitySchemaVersion,
		Stage:      "route",
		RouteText:  "thời tiết Hà Nội",
		ActualMode: "chat",
	})
	if err != nil || match || reason != "route_mode" {
		t.Fatalf("match=%v reason=%s err=%v", match, reason, err)
	}
	snapshot := engine.snapshot()
	if snapshot.RouteTotal != 2 || snapshot.RouteMatch != 1 || snapshot.RouteMiss != 1 {
		t.Fatalf("snapshot=%+v", snapshot)
	}
}

func TestAtriParityVertexPlanAutoAndOverrides(t *testing.T) {
	engine := newAtriParityEngine()
	match, reason, err := engine.evaluate(atriParityEvent{
		Version:          atriParitySchemaVersion,
		Stage:            "vertex_plan",
		Mode:             "code",
		RuntimeModel:     "gemini-3.5-flash-lite",
		BaseModel:        "gemini-3.6-flash",
		ResolvedModel:    "gemini-3.6-flash",
		ThinkingAuto:     true,
		ThinkingLevels:   map[string]string{"code": "low"},
		BaseThinking:     "high",
		ProviderModel:    "auto",
		ProviderThinking: "auto",
		ResolvedThinking: "high",
		ToolProfile:      "code_plugins",
	})
	if err != nil || !match || reason != "ok" {
		t.Fatalf("match=%v reason=%s err=%v", match, reason, err)
	}

	match, reason, err = engine.evaluate(atriParityEvent{
		Version:          atriParitySchemaVersion,
		Stage:            "vertex_plan",
		Mode:             "tools",
		RuntimeModel:     "gemini-3.5-flash-lite",
		BaseModel:        "gemini-3.5-flash-lite",
		ResolvedModel:    "gemini-3.1-pro-preview",
		ThinkingAuto:     false,
		ThinkingLevels:   map[string]string{"tools": "medium"},
		BaseThinking:     "medium",
		ProviderModel:    "gemini-3.1-pro-preview",
		ProviderThinking: "low",
		ResolvedThinking: "low",
		ToolProfile:      "tool_functions",
	})
	if err != nil || !match || reason != "ok" {
		t.Fatalf("override match=%v reason=%s err=%v", match, reason, err)
	}

	match, reason, err = engine.evaluate(atriParityEvent{
		Version:          atriParitySchemaVersion,
		Stage:            "vertex_plan",
		Mode:             "web",
		RuntimeModel:     "gemini-3.5-flash-lite",
		BaseModel:        "wrong-model",
		ResolvedModel:    "gemini-3.5-flash-lite",
		ThinkingAuto:     true,
		BaseThinking:     "high",
		ProviderModel:    "auto",
		ProviderThinking: "auto",
		ResolvedThinking: "high",
		ToolProfile:      "google_search",
	})
	if err != nil || match || reason != "base_model" {
		t.Fatalf("mismatch match=%v reason=%s err=%v", match, reason, err)
	}
}

func TestAtriParityToolBoundary(t *testing.T) {
	for _, tt := range []struct {
		mode    string
		profile string
		name    string
		want    bool
	}{
		{"code", "code_plugins", "code_plugin_call", true},
		{"code", "code_plugins", "weather", false},
		{"tools", "tool_functions", "weather", true},
		{"tools", "tool_functions", "code_plugin_call", false},
		{"web", "google_search", "weather", false},
	} {
		if got := validateObservedTool(tt.mode, tt.profile, tt.name); got != tt.want {
			t.Fatalf("mode=%s profile=%s name=%s got=%v want=%v", tt.mode, tt.profile, tt.name, got, tt.want)
		}
	}
}
