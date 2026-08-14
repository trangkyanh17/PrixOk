package runtimecfg

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGoogleChunkTextBoundsAndLimit(t *testing.T) {
	text := strings.Repeat("xin chào ", 4000)
	chunks := googleChunkText(text, 90)
	if len(chunks) != 20 {
		t.Fatalf("chunks=%d", len(chunks))
	}
	for _, chunk := range chunks {
		if len([]rune(chunk)) > 90 {
			t.Fatalf("chunk too long: %d", len([]rune(chunk)))
		}
	}
}

func TestGoogleCloudTranslateUsesServiceAccountAndReturnsParityShape(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}

	tokenRequests := 0
	tokenServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tokenRequests++
		if r.Method != http.MethodPost {
			t.Fatalf("token method=%s", r.Method)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "cloud-token",
			"expires_in":   3600,
		})
	}))
	defer tokenServer.Close()

	translationRequests := 0
	translationServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		translationRequests++
		if r.Method != http.MethodPost {
			t.Fatalf("translation method=%s", r.Method)
		}
		if r.Header.Get("Authorization") != "Bearer cloud-token" {
			t.Fatalf("authorization=%q", r.Header.Get("Authorization"))
		}
		if r.URL.Path != "/projects/project-x/locations/global:translateText" {
			t.Fatalf("path=%q", r.URL.Path)
		}
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		if body["targetLanguageCode"] != "en" || body["mimeType"] != "text/plain" {
			t.Fatalf("body=%v", body)
		}
		contents := body["contents"].([]any)
		if translationRequests == 1 {
			if body["sourceLanguageCode"] != "vi" || len(contents) != 2 {
				t.Fatalf("first body=%v", body)
			}
		} else {
			if _, ok := body["sourceLanguageCode"]; ok || len(contents) != 1 {
				t.Fatalf("second body=%v", body)
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"translations": []any{
				map[string]any{
					"translatedText":       "Hello",
					"detectedLanguageCode": "vi",
				},
				map[string]any{
					"translatedText": "world",
				},
			},
		})
	}))
	defer translationServer.Close()

	path := writeServiceAccountFile(t, t.TempDir(), privateKey, tokenServer.URL)
	credentials := NewServiceAccountTokenProvider(path)
	credentials.Client = tokenServer.Client()

	runtime := GoogleCloudToolRuntime{
		Client:             translationServer.Client(),
		Credentials:        credentials,
		TranslationBaseURL: translationServer.URL,
	}
	text := strings.Repeat("a", 900) + "\n" + strings.Repeat("b", 20)
	result := runtime.Translate(context.Background(), text, " en ", " vi ")
	if result["ok"] != true || result["source"] != "Google Cloud Translation v3" {
		t.Fatalf("result=%v", result)
	}
	if result["target_language"] != "en" || result["detected_language"] != "vi" || result["translated_text"] != "Hello\nworld" {
		t.Fatalf("translation=%v", result)
	}
	if tokenRequests != 1 {
		t.Fatalf("token requests=%d", tokenRequests)
	}

	second := runtime.Translate(context.Background(), "xin chào", "en", "")
	if second["ok"] != true {
		t.Fatalf("second=%v", second)
	}
	if tokenRequests != 1 {
		t.Fatalf("credential cache missed requests=%d", tokenRequests)
	}
	if translationRequests != 2 {
		t.Fatalf("translation requests=%d", translationRequests)
	}
}

func TestGoogleCloudTranslateValidationAndRegistry(t *testing.T) {
	registry := NewToolRegistry()
	runtime := GoogleCloudToolRuntime{}
	if err := RegisterGoogleCloudTools(registry, runtime); err != nil {
		t.Fatal(err)
	}
	if !registry.Has("google_translate") {
		t.Fatal("translate tool not registered")
	}

	result := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"google_translate",
		map[string]any{},
		false,
	).(map[string]any)
	if result["ok"] != false || result["code"] != "NOT_CONFIGURED" {
		t.Fatalf("result=%v", result)
	}
}
