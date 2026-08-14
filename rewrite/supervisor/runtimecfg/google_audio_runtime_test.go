package runtimecfg

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGoogleAudioTranscribeAndGeminiPart(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	tokenServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "speech-token",
			"expires_in":   3600,
		})
	}))
	defer tokenServer.Close()

	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/projects/project-x/locations/global/recognizers/_:recognize" {
			t.Fatalf("path=%q", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer speech-token" {
			t.Fatalf("authorization=%q", r.Header.Get("Authorization"))
		}
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		decoded, err := base64.StdEncoding.DecodeString(body["content"].(string))
		if err != nil || string(decoded) != "audio-data" {
			t.Fatalf("audio=%q err=%v", string(decoded), err)
		}
		config := body["config"].(map[string]any)
		if config["model"] != "long" {
			t.Fatalf("config=%v", config)
		}
		languages := config["languageCodes"].([]any)
		if len(languages) != 2 || languages[0] != "vi-VN" || languages[1] != "en-US" {
			t.Fatalf("languages=%v", languages)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"results": []any{
				map[string]any{
					"alternatives": []any{
						map[string]any{"transcript": "Xin chào"},
					},
				},
				map[string]any{
					"alternatives": []any{
						map[string]any{"transcript": "Atri"},
					},
				},
			},
		})
	}))
	defer apiServer.Close()

	path := writeServiceAccountFile(t, t.TempDir(), privateKey, tokenServer.URL)
	credentials := NewServiceAccountTokenProvider(path)
	credentials.Client = tokenServer.Client()
	runtime := GoogleAudioRuntime{
		Client:        apiServer.Client(),
		Credentials:   credentials,
		SpeechBaseURL: apiServer.URL,
	}
	transcript, err := runtime.Transcribe(context.Background(), []byte("audio-data"))
	if err != nil || transcript != "Xin chào Atri" {
		t.Fatalf("transcript=%q err=%v", transcript, err)
	}

	part := BuildGeminiAudioPart([]byte("audio-data"), "audio/ogg")
	inline := part["inlineData"].(map[string]any)
	if inline["mimeType"] != "audio/ogg" {
		t.Fatalf("part=%v", part)
	}
	decoded, err := base64.StdEncoding.DecodeString(inline["data"].(string))
	if err != nil || string(decoded) != "audio-data" {
		t.Fatalf("decoded=%q err=%v", string(decoded), err)
	}
}

func TestGoogleAudioValidation(t *testing.T) {
	runtime := GoogleAudioRuntime{}
	if _, err := runtime.Transcribe(context.Background(), []byte("audio")); err == nil {
		t.Fatal("missing credentials should fail")
	}

	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	tokenServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "token",
			"expires_in":   3600,
		})
	}))
	defer tokenServer.Close()
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, tokenServer.URL)
	credentials := NewServiceAccountTokenProvider(path)
	credentials.Client = tokenServer.Client()
	runtime.Credentials = credentials
	if transcript, err := runtime.Transcribe(context.Background(), nil); err != nil || transcript != "" {
		t.Fatalf("empty transcript=%q err=%v", transcript, err)
	}

	if BuildGeminiAudioPart(nil, "") != nil {
		t.Fatal("empty Gemini audio part should be nil")
	}
	part := BuildGeminiAudioPart([]byte("x"), "")
	if !strings.Contains(part["inlineData"].(map[string]any)["mimeType"].(string), "audio/") {
		t.Fatalf("part=%v", part)
	}
}
