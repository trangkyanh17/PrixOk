package runtimecfg

import "testing"

func TestResolveModelAliases(t *testing.T) {
	cases := map[string]string{
		"flash": "gemini-3.6-flash",
		"pro":   "gemini-3.1-pro-preview",
		"lite":  "gemini-3.5-flash-lite",
	}
	for input, want := range cases {
		spec, ok := ResolveModel(input)
		if !ok || spec.Model != want {
			t.Fatalf("input=%q spec=%+v ok=%v", input, spec, ok)
		}
	}
}

func TestResolveThinkingPerModel(t *testing.T) {
	pro, ok := ResolveModel("pro")
	if !ok {
		t.Fatal("pro model missing")
	}
	if got, ok := ResolveThinking(pro, "default"); !ok || got != "high" {
		t.Fatalf("default=%q ok=%v", got, ok)
	}
	if _, ok := ResolveThinking(pro, "minimal"); ok {
		t.Fatal("pro must reject minimal thinking")
	}
}
