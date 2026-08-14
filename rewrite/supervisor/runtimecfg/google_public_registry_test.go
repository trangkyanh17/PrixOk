package runtimecfg

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestNormalizedGooglePublicToolsDefaultOptionalArguments(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("regionCode") != "VN" {
			t.Fatalf("region=%q", r.URL.Query().Get("regionCode"))
		}
		if r.URL.Query().Get("q") != "atri" {
			t.Fatalf("query=%q", r.URL.Query().Get("q"))
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"items": []any{}})
	}))
	defer server.Close()

	registry := NewToolRegistry()
	runtime := GooglePublicToolRuntime{
		Client:           server.Client(),
		Values:           map[string]string{"YOUTUBE_API_KEY": "key"},
		YouTubeSearchURL: server.URL,
	}
	if err := RegisterNormalizedGooglePublicTools(registry, runtime); err != nil {
		t.Fatal(err)
	}

	result := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"google_youtube_search",
		map[string]any{"query": "atri"},
		false,
	).(map[string]any)
	if result["ok"] != true {
		t.Fatalf("result=%v", result)
	}
}

func TestNormalizedGooglePublicToolsRejectMissingRequiredQuery(t *testing.T) {
	registry := NewToolRegistry()
	runtime := GooglePublicToolRuntime{
		Values: map[string]string{
			"YOUTUBE_API_KEY": "key",
			"GOOGLE_API_KEY":  "key",
		},
	}
	if err := RegisterNormalizedGooglePublicTools(registry, runtime); err != nil {
		t.Fatal(err)
	}

	youtube := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"google_youtube_search",
		map[string]any{},
		false,
	).(map[string]any)
	if youtube["ok"] != false || youtube["error"] != "Query YouTube rỗng." {
		t.Fatalf("youtube=%v", youtube)
	}

	books := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"google_books_search",
		map[string]any{},
		false,
	).(map[string]any)
	if books["ok"] != false || books["error"] != "Query sách rỗng." {
		t.Fatalf("books=%v", books)
	}
}
