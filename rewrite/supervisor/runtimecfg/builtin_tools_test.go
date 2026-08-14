package runtimecfg

import "testing"

func TestNewBuiltinToolRegistryRegistersPortedTools(t *testing.T) {
	registry, err := NewBuiltinToolRegistry(BuiltinToolOptions{})
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{
		"get_weather",
		"google_youtube_search",
		"google_safe_browsing",
		"google_books_search",
		"google_places_search",
		"google_route",
		"google_geocode",
		"google_translate",
		"google_capabilities",
		"google_drive_search",
		"google_drive_read_text",
		"google_calendar_events",
		"google_gmail_search",
		"google_gmail_read",
		"google_sheets_read",
		"google_tts_speak",
		"google_vision_ocr",
		"google_document_ai",
		"search_delta_force_cn",
		"get_delta_force_cn_history",
		"compare_delta_force_cn_seasons",
	} {
		if !registry.Has(name) {
			t.Fatalf("missing builtin tool %s", name)
		}
	}
	if declarations := registry.Declarations("chat", false); len(declarations) != 15 {
		t.Fatalf("public declarations=%v", declarations)
	}
	if declarations := registry.Declarations("chat", true); len(declarations) != 21 {
		t.Fatalf("owner declarations=%v", declarations)
	}
}
