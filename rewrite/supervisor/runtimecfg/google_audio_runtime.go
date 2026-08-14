package runtimecfg

import (
	"context"
	"encoding/base64"
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

const DefaultGoogleSpeechBaseURL = "https://speech.googleapis.com/v2"

type GoogleAudioRuntime struct {
	Client        HTTPDoer
	Credentials   *ServiceAccountTokenProvider
	SpeechBaseURL string
}

func (runtime GoogleAudioRuntime) recognizeURL(project string) string {
	base := strings.TrimRight(strings.TrimSpace(runtime.SpeechBaseURL), "/")
	if base == "" {
		base = DefaultGoogleSpeechBaseURL
	}
	return base + "/projects/" + url.PathEscape(project) +
		"/locations/global/recognizers/_:recognize"
}

func (runtime GoogleAudioRuntime) Transcribe(
	ctx context.Context,
	data []byte,
) (string, error) {
	if runtime.Credentials == nil {
		return "", fmt.Errorf("GOOGLE_APPLICATION_CREDENTIALS chưa cấu hình")
	}
	if len(data) == 0 {
		return "", nil
	}
	if len(data) > 10*1024*1024 {
		return "", fmt.Errorf("audio vượt quá giới hạn 10 MiB")
	}
	project, err := runtime.Credentials.ProjectID()
	if err != nil || strings.TrimSpace(project) == "" {
		return "", fmt.Errorf("GOOGLE_CLOUD_PROJECT/VERTEX_PROJECT_ID chưa cấu hình")
	}
	token, err := runtime.Credentials.Token(ctx, false)
	if err != nil {
		return "", err
	}

	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodPost,
		runtime.recognizeURL(project),
		map[string]string{
			"Authorization": "Bearer " + token,
			"Content-Type":  "application/json",
		},
		nil,
		map[string]any{
			"config": map[string]any{
				"autoDecodingConfig": map[string]any{},
				"model":              "long",
				"languageCodes":      []any{"vi-VN", "en-US"},
				"features": map[string]any{
					"enableAutomaticPunctuation": true,
				},
			},
			"content": base64.StdEncoding.EncodeToString(data),
		},
	)
	if err != nil {
		return "", err
	}

	parts := make([]string, 0)
	for _, rawResult := range weatherSlice(payload["results"]) {
		result := weatherMap(rawResult)
		alternatives := weatherSlice(result["alternatives"])
		if len(alternatives) == 0 {
			continue
		}
		transcript := strings.TrimSpace(
			googleString(weatherMap(alternatives[0])["transcript"]),
		)
		if transcript != "" {
			parts = append(parts, transcript)
		}
	}
	return strings.TrimSpace(strings.Join(parts, " ")), nil
}

func BuildGeminiAudioPart(data []byte, mimeType string) map[string]any {
	if len(data) == 0 || len(data) > 20*1024*1024 {
		return nil
	}
	mimeType = strings.TrimSpace(mimeType)
	if mimeType == "" {
		mimeType = "audio/ogg"
	}
	return map[string]any{
		"inlineData": map[string]any{
			"mimeType": mimeType,
			"data":     base64.StdEncoding.EncodeToString(data),
		},
	}
}
