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
	} {
		if !registry.Has(name) {
			t.Fatalf("missing builtin tool %s", name)
		}
	}
	if declarations := registry.Declarations("chat", false); len(declarations) != 7 {
		t.Fatalf("declarations=%v", declarations)
	}
}
