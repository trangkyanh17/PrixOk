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

func newGoogleMediaTestCredentials(
	t *testing.T,
) (*ServiceAccountTokenProvider, *httptest.Server) {
	t.Helper()
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	tokenServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "cloud-token",
			"expires_in":   3600,
		})
	}))
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, tokenServer.URL)
	credentials := NewServiceAccountTokenProvider(path)
	credentials.Client = tokenServer.Client()
	return credentials, tokenServer
}

func TestGoogleMediaTTSVisionAndDocumentAI(t *testing.T) {
	credentials, tokenServer := newGoogleMediaTestCredentials(t)
	defer tokenServer.Close()

	apiServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer cloud-token" {
			t.Fatalf("authorization=%q", r.Header.Get("Authorization"))
		}
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/tts":
			var body map[string]any
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			voice := body["voice"].(map[string]any)
			if voice["languageCode"] != "vi-VN" || voice["name"] != "vi-VN-Neural2-A" {
				t.Fatalf("voice=%v", voice)
			}
			config := body["audioConfig"].(map[string]any)
			if config["audioEncoding"] != "OGG_OPUS" {
				t.Fatalf("audio config=%v", config)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"audioContent": base64.StdEncoding.EncodeToString([]byte("ogg-data")),
			})
		case r.URL.Path == "/vision":
			var body map[string]any
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			requests := body["requests"].([]any)
			request := requests[0].(map[string]any)
			image := request["image"].(map[string]any)
			decoded, err := base64.StdEncoding.DecodeString(image["content"].(string))
			if err != nil || string(decoded) != "image-data" {
				t.Fatalf("image=%q err=%v", string(decoded), err)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"responses": []any{
					map[string]any{
						"fullTextAnnotation": map[string]any{
							"text":  "Xin chào OCR",
							"pages": []any{map[string]any{}, map[string]any{}},
						},
					},
				},
			})
		case strings.Contains(r.URL.Path, "/processors/processor-1:process"):
			if !strings.Contains(r.URL.Path, "/projects/project-x/locations/asia-southeast1/") {
				t.Fatalf("document path=%q", r.URL.Path)
			}
			var body map[string]any
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			rawDocument := body["rawDocument"].(map[string]any)
			if rawDocument["mimeType"] != "application/pdf" {
				t.Fatalf("raw document=%v", rawDocument)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"document": map[string]any{
					"text": "Document text",
					"entities": []any{
						map[string]any{
							"type":            "invoice_id",
							"mentionText":     "INV-1",
							"confidence":      0.98,
							"normalizedValue": map[string]any{"text": "INV-1"},
						},
					},
				},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer apiServer.Close()

	var sentAudio []byte
	var sentName string
	runtime := GoogleMediaToolRuntime{
		Client:      apiServer.Client(),
		Credentials: credentials,
		Values: map[string]string{
			"GOOGLE_DOCUMENT_AI_PROCESSOR_ID": "processor-1",
			"GOOGLE_DOCUMENT_AI_LOCATION":     "asia-southeast1",
		},
		TTSURL:            apiServer.URL + "/tts",
		VisionURL:         apiServer.URL + "/vision",
		DocumentAIBaseURL: apiServer.URL,
		VoiceSender: func(_ context.Context, _ ToolContext, audio []byte, name string) error {
			sentAudio = append([]byte(nil), audio...)
			sentName = name
			return nil
		},
	}

	tts := runtime.SendTTSVoice(
		context.Background(),
		ToolContext{Mode: "chat"},
		"Xin chào",
		"vi-VN",
		"vi-VN-Neural2-A",
	)
	if tts["ok"] != true || tts["sent"] != true || string(sentAudio) != "ogg-data" || sentName != "atri-google-tts.ogg" {
		t.Fatalf("tts=%v audio=%q name=%q", tts, string(sentAudio), sentName)
	}

	vision := runtime.VisionOCR(
		context.Background(),
		ToolContext{
			Mode: "chat",
			Metadata: map[string]any{
				"attachment_bytes":     []byte("image-data"),
				"attachment_mime_type": "image/png",
			},
		},
	)
	if vision["ok"] != true || vision["text"] != "Xin chào OCR" || vision["pages"] != 2 {
		t.Fatalf("vision=%v", vision)
	}

	document := runtime.DocumentAI(
		context.Background(),
		ToolContext{
			Mode: "chat",
			Metadata: map[string]any{
				"attachment_bytes":     []byte("pdf-data"),
				"attachment_mime_type": "application/pdf",
			},
		},
	)
	if document["ok"] != true || document["text"] != "Document text" {
		t.Fatalf("document=%v", document)
	}
	entities := document["entities"].([]any)
	if len(entities) != 1 || entities[0].(map[string]any)["mention_text"] != "INV-1" {
		t.Fatalf("entities=%v", entities)
	}
}

func TestGoogleMediaToolValidationAndRegistry(t *testing.T) {
	runtime := GoogleMediaToolRuntime{}
	if result := runtime.SendTTSVoice(
		context.Background(),
		ToolContext{Mode: "chat"},
		"hello",
		"",
		"",
	); result["code"] != "RUNTIME_NOT_WIRED" {
		t.Fatalf("tts=%v", result)
	}

	if result := runtime.VisionOCR(
		context.Background(),
		ToolContext{Mode: "chat"},
	); result["code"] != "NOT_CONFIGURED" {
		t.Fatalf("vision=%v", result)
	}

	registry := NewToolRegistry()
	if err := RegisterGoogleMediaTools(registry, runtime); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{
		"google_tts_speak",
		"google_vision_ocr",
		"google_document_ai",
	} {
		if !registry.Has(name) {
			t.Fatalf("missing tool %s", name)
		}
	}
	if declarations := registry.Declarations("chat", false); len(declarations) != 3 {
		t.Fatalf("declarations=%v", declarations)
	}
}

func TestGoogleMediaAttachmentValidation(t *testing.T) {
	credentials, tokenServer := newGoogleMediaTestCredentials(t)
	defer tokenServer.Close()
	runtime := GoogleMediaToolRuntime{Credentials: credentials}

	missing := runtime.VisionOCR(
		context.Background(),
		ToolContext{Mode: "chat", Metadata: map[string]any{}},
	)
	if missing["ok"] != false || !strings.Contains(missing["error"].(string), "Hãy gửi/reply") {
		t.Fatalf("missing=%v", missing)
	}

	nonImage := runtime.VisionOCR(
		context.Background(),
		ToolContext{
			Mode: "chat",
			Metadata: map[string]any{
				"attachment_bytes":     []byte("pdf"),
				"attachment_mime_type": "application/pdf",
			},
		},
	)
	if nonImage["ok"] != false || !strings.Contains(nonImage["error"].(string), "chỉ nhận ảnh") {
		t.Fatalf("non image=%v", nonImage)
	}
}
