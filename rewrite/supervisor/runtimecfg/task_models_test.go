package runtimecfg

import "testing"

func TestTaskModelCandidatesDefaultsToChat(t *testing.T) {
	got := TaskModelCandidates("missing", true, map[ProviderModel]string{})
	want := TaskModelOrder["chat"]
	if len(got) != len(want) {
		t.Fatalf("got=%v want=%v", got, want)
	}
	for index := range want {
		if got[index] != want[index] {
			t.Fatalf("got=%v want=%v", got, want)
		}
	}
}

func TestTaskModelCandidatesDropsDeadModels(t *testing.T) {
	status := map[ProviderModel]string{
		{Provider: "groq", Model: "qwen/qwen3.6-27b"}: "dead",
	}
	got := TaskModelCandidates("coding", true, status)
	if len(got) != 2 {
		t.Fatalf("unexpected candidates: %v", got)
	}
	if got[0].Provider != "cerebras" || got[1].Model != "cohere/north-mini-code:free" {
		t.Fatalf("unexpected candidates: %v", got)
	}
}

func TestToolsKeepsVertexAuto(t *testing.T) {
	got := TaskModelCandidates("tools", true, map[ProviderModel]string{
		{Provider: "vertex", Model: "auto"}: "dead",
	})
	if len(got) != 1 || got[0].Provider != "vertex" || got[0].Model != "auto" {
		t.Fatalf("unexpected tools candidates: %v", got)
	}
}

func TestModelMetadataReturnsCopy(t *testing.T) {
	first, ok := ModelMetadataFor("groq", "qwen/qwen3.6-27b")
	if !ok {
		t.Fatal("metadata missing")
	}
	if first.Context != 131072 || first.MaxOutput != 16384 || first.Adapter != "groq_qwen36" {
		t.Fatalf("metadata=%#v", first)
	}
	first.Capabilities[0] = "mutated"
	second, _ := ModelMetadataFor("groq", "qwen/qwen3.6-27b")
	if second.Capabilities[0] != "chat" {
		t.Fatalf("registry was mutated: %#v", second)
	}
}
