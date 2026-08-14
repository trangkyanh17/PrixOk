package runtimecfg

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

const DefaultGoogleTranslationBaseURL = "https://translation.googleapis.com/v3"

type GoogleCloudToolRuntime struct {
	Client             HTTPDoer
	Credentials        *ServiceAccountTokenProvider
	TranslationBaseURL string
}

func GoogleTranslateDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_translate",
		"description": "Dịch văn bản bằng Google Cloud Translation.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"text":            map[string]any{"type": "string"},
				"target_language": map[string]any{"type": "string"},
				"source_language": map[string]any{"type": "string"},
			},
			"required": []any{"text", "target_language"},
		},
	}
}

func googleChunkText(text string, size int) []string {
	text = strings.TrimSpace(text)
	if size <= 0 {
		size = 900
	}
	chunks := make([]string, 0, 20)
	for text != "" && len(chunks) < 20 {
		runes := []rune(text)
		if len(runes) <= size {
			chunks = append(chunks, text)
			break
		}

		cut := size
		prefix := string(runes[:size])
		if index := strings.LastIndex(prefix, "\n"); index >= size/3 {
			cut = len([]rune(prefix[:index]))
		} else if index := strings.LastIndex(prefix, " "); index >= size/3 {
			cut = len([]rune(prefix[:index]))
		}
		if cut <= 0 {
			cut = size
		}
		chunk := strings.TrimSpace(string(runes[:cut]))
		if chunk != "" {
			chunks = append(chunks, chunk)
		}
		text = strings.TrimSpace(string(runes[cut:]))
	}
	return chunks
}

func googleEscapePath(value string) string {
	return url.PathEscape(strings.TrimSpace(value))
}

func (runtime GoogleCloudToolRuntime) translationURL(projectID string) string {
	base := strings.TrimRight(strings.TrimSpace(runtime.TranslationBaseURL), "/")
	if base == "" {
		base = DefaultGoogleTranslationBaseURL
	}
	return fmt.Sprintf(
		"%s/projects/%s/locations/global:translateText",
		base,
		googleEscapePath(projectID),
	)
}

func (runtime GoogleCloudToolRuntime) Translate(
	ctx context.Context,
	text string,
	targetLanguage string,
	sourceLanguage string,
) map[string]any {
	if runtime.Credentials == nil {
		return googleToolError(
			"Thiếu GOOGLE_APPLICATION_CREDENTIALS.",
			"NOT_CONFIGURED",
		)
	}
	projectID, err := runtime.Credentials.ProjectID()
	if err != nil || strings.TrimSpace(projectID) == "" {
		return googleToolError(
			"Thiếu VERTEX_PROJECT_ID/GOOGLE_CLOUD_PROJECT.",
			"NOT_CONFIGURED",
		)
	}

	targetLanguage = strings.TrimSpace(targetLanguage)
	if targetLanguage == "" {
		return googleToolError("Thiếu target_language.", "")
	}
	chunks := googleChunkText(text, 900)
	if len(chunks) == 0 {
		return googleToolError("Văn bản cần dịch rỗng.", "")
	}

	token, err := runtime.Credentials.Token(ctx, false)
	if err != nil {
		return googleToolError(err.Error(), "")
	}

	body := map[string]any{
		"contents":           chunks,
		"mimeType":           "text/plain",
		"targetLanguageCode": targetLanguage,
	}
	sourceLanguage = strings.TrimSpace(sourceLanguage)
	if sourceLanguage != "" {
		body["sourceLanguageCode"] = sourceLanguage
	}

	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodPost,
		runtime.translationURL(projectID),
		map[string]string{
			"Authorization": "Bearer " + token,
			"Content-Type":  "application/json",
		},
		nil,
		body,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}

	translations := make([]string, 0)
	detectedLanguage := ""
	for _, rawItem := range weatherSlice(payload["translations"]) {
		item := weatherMap(rawItem)
		translations = append(translations, googleString(item["translatedText"]))
		if detectedLanguage == "" {
			detectedLanguage = googleString(item["detectedLanguageCode"])
		}
	}

	return googleToolOK(map[string]any{
		"source":            "Google Cloud Translation v3",
		"target_language":   targetLanguage,
		"detected_language": detectedLanguage,
		"translated_text":   strings.TrimSpace(strings.Join(translations, "\n")),
	})
}

func (runtime GoogleCloudToolRuntime) RegisteredTools() []RegisteredTool {
	return []RegisteredTool{
		{
			Name:        "google_translate",
			Declaration: GoogleTranslateDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.Translate(
					ctx,
					googleString(arguments["text"]),
					googleString(arguments["target_language"]),
					googleString(arguments["source_language"]),
				), nil
			},
		},
	}
}

func RegisterGoogleCloudTools(registry *ToolRegistry, runtime GoogleCloudToolRuntime) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	for _, tool := range runtime.RegisteredTools() {
		if err := registry.Register(tool); err != nil {
			return err
		}
	}
	return nil
}
