package runtimecfg

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGooglePublicToolsRequireConfiguredKeys(t *testing.T) {
	runtime := GooglePublicToolRuntime{}
	youtube := runtime.YouTubeSearch(context.Background(), "atri", 5, "VN")
	if youtube["ok"] != false || youtube["code"] != "NOT_CONFIGURED" {
		t.Fatalf("youtube=%v", youtube)
	}
	safe := runtime.SafeBrowsing(context.Background(), []string{"https://example.com"})
	if safe["ok"] != false || safe["code"] != "NOT_CONFIGURED" {
		t.Fatalf("safe=%v", safe)
	}
}

func TestGooglePublicToolsYouTubeSafeBrowsingAndBooks(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/youtube":
			if r.URL.Query().Get("q") != "atri bot" || r.URL.Query().Get("safeSearch") != "moderate" || r.URL.Query().Get("regionCode") != "VN" || r.URL.Query().Get("maxResults") != "10" {
				t.Fatalf("youtube query=%v", r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"items": []any{
					map[string]any{
						"id": map[string]any{"videoId": "abc123"},
						"snippet": map[string]any{
							"title":        "Atri demo",
							"channelTitle": "Prix",
							"publishedAt":  "2026-08-14T00:00:00Z",
							"description":  "demo",
						},
					},
				},
			})
		case "/safe":
			values := r.URL.Query()["urls[]"]
			if len(values) != 2 || values[0] != "https://bad.example" || values[1] != "http://ok.example" {
				t.Fatalf("safe urls=%v query=%v", values, r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"threats":       []any{map[string]any{"threatType": "MALWARE"}},
				"cacheDuration": "300s",
			})
		case "/books":
			if r.URL.Query().Get("q") != "Go systems" || r.URL.Query().Get("maxResults") != "5" || r.URL.Query().Get("printType") != "books" || r.URL.Query().Get("key") != "google-key" {
				t.Fatalf("books query=%v", r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"items": []any{
					map[string]any{
						"id": "book-1",
						"volumeInfo": map[string]any{
							"title":         "Go Systems",
							"authors":       []any{"A"},
							"publisher":     "P",
							"publishedDate": "2026",
							"description":   "book description",
							"industryIdentifiers": []any{
								map[string]any{"type": "ISBN_13", "identifier": "9780000000001"},
							},
							"infoLink": "https://books.example/book-1",
						},
					},
				},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	runtime := GooglePublicToolRuntime{
		Client: server.Client(),
		Values: map[string]string{
			"YOUTUBE_API_KEY":       "youtube-key",
			"SAFE_BROWSING_API_KEY": "safe-key",
			"GOOGLE_API_KEY":        "google-key",
		},
		YouTubeSearchURL: server.URL + "/youtube",
		SafeBrowsingURL:  server.URL + "/safe",
		BooksURL:         server.URL + "/books",
	}

	youtube := runtime.YouTubeSearch(context.Background(), " atri bot ", 99, "vnx")
	if youtube["ok"] != true || youtube["source"] != "YouTube Data API v3" {
		t.Fatalf("youtube=%v", youtube)
	}
	videos := youtube["results"].([]any)
	if len(videos) != 1 || videos[0].(map[string]any)["url"] != "https://www.youtube.com/watch?v=abc123" {
		t.Fatalf("videos=%v", videos)
	}

	safe := runtime.SafeBrowsing(context.Background(), []string{"ftp://ignore", " https://bad.example ", "http://ok.example"})
	if safe["ok"] != true || safe["unsafe"] != true || len(safe["checked_urls"].([]string)) != 2 {
		t.Fatalf("safe=%v", safe)
	}

	books := runtime.BooksSearch(context.Background(), " Go systems ", 5)
	if books["ok"] != true || books["source"] != "Google Books API" {
		t.Fatalf("books=%v", books)
	}
	bookResults := books["results"].([]any)
	if len(bookResults) != 1 || bookResults[0].(map[string]any)["title"] != "Go Systems" {
		t.Fatalf("book results=%v", bookResults)
	}
}

func TestRegisterGooglePublicTools(t *testing.T) {
	registry := NewToolRegistry()
	if err := RegisterGooglePublicTools(registry, GooglePublicToolRuntime{}); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"google_youtube_search", "google_safe_browsing", "google_books_search"} {
		if !registry.Has(name) {
			t.Fatalf("missing tool %s", name)
		}
	}
	declarations := registry.Declarations("chat", false)
	if len(declarations) != 3 {
		t.Fatalf("declarations=%v", declarations)
	}
	invalid := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"google_safe_browsing",
		map[string]any{"urls": []any{"not-a-url"}},
		false,
	).(map[string]any)
	if invalid["ok"] != false || !strings.Contains(invalid["error"].(string), "Thiếu SAFE_BROWSING_API_KEY") {
		t.Fatalf("invalid=%v", invalid)
	}
}
