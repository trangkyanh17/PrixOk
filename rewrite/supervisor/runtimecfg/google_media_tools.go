package runtimecfg

import (
	"context"
	"encoding/base64"
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

const (
	DefaultGoogleTTSURL    = "https://texttospeech.googleapis.com/v1/text:synthesize"
	DefaultGoogleVisionURL = "https://vision.googleapis.com/v1/images:annotate"
)

type GoogleVoiceSender func(
	context.Context,
	ToolContext,
	[]byte,
	string,
) error

type GoogleMediaToolRuntime struct {
	Client            HTTPDoer
	Credentials       *ServiceAccountTokenProvider
	Values            map[string]string
	TTSURL            string
	VisionURL         string
	DocumentAIBaseURL string
	VoiceSender       GoogleVoiceSender
}

func GoogleTTSDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_tts_speak",
		"description": "Chuyển văn bản thành giọng nói và gửi voice Telegram. Chỉ gọi khi người dùng yêu cầu trả lời bằng giọng.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"text":          map[string]any{"type": "string"},
				"language_code": map[string]any{"type": "string"},
				"voice_name":    map[string]any{"type": "string"},
			},
			"required": []any{"text"},
		},
	}
}

func GoogleVisionOCRDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_vision_ocr",
		"description": "OCR ảnh/tài liệu ảnh đang gửi hoặc đang reply bằng Google Cloud Vision. Dùng khi cần trích xuất chữ chính xác từ ảnh.",
		"parameters": map[string]any{
			"type":       "object",
			"properties": map[string]any{},
			"required":   []any{},
		},
	}
}

func GoogleDocumentAIDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_document_ai",
		"description": "Phân tích tài liệu/PDF/ảnh đang gửi hoặc đang reply bằng Google Document AI. Chỉ dùng khi Document AI processor đã cấu hình.",
		"parameters": map[string]any{
			"type":       "object",
			"properties": map[string]any{},
			"required":   []any{},
		},
	}
}

func (runtime GoogleMediaToolRuntime) setting(names ...string) string {
	return (GooglePublicToolRuntime{Values: runtime.Values}).setting(names...)
}

func googleMediaCredentialsError() map[string]any {
	return googleToolError("Thiếu GOOGLE_APPLICATION_CREDENTIALS.", "NOT_CONFIGURED")
}

func googleMediaAttachment(toolContext ToolContext) ([]byte, string, bool) {
	metadata := toolContext.Metadata
	if metadata == nil {
		return nil, "", false
	}

	var data []byte
	switch value := metadata["attachment_bytes"].(type) {
	case []byte:
		data = append([]byte(nil), value...)
	case string:
		decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(value))
		if err == nil {
			data = decoded
		}
	}
	if len(data) == 0 {
		if encoded := googleString(metadata["attachment_base64"]); encoded != "" {
			decoded, err := base64.StdEncoding.DecodeString(encoded)
			if err == nil {
				data = decoded
			}
		}
	}
	if len(data) == 0 || len(data) > 15*1024*1024 {
		return nil, "", false
	}
	mimeType := googleString(metadata["attachment_mime_type"])
	if mimeType == "" {
		mimeType = "image/jpeg"
	}
	return data, mimeType, true
}

func googleVoiceSenderFromContext(
	runtime GoogleMediaToolRuntime,
	toolContext ToolContext,
) GoogleVoiceSender {
	if runtime.VoiceSender != nil {
		return runtime.VoiceSender
	}
	if toolContext.Metadata == nil {
		return nil
	}
	if sender, ok := toolContext.Metadata["voice_sender"].(GoogleVoiceSender); ok {
		return sender
	}
	if sender, ok := toolContext.Metadata["voice_sender"].(func(context.Context, ToolContext, []byte, string) error); ok {
		return GoogleVoiceSender(sender)
	}
	return nil
}

func googleTrimRunes(value string, limit int) string {
	value = strings.TrimSpace(value)
	if limit <= 0 {
		return value
	}
	runes := []rune(value)
	if len(runes) <= limit {
		return value
	}
	return string(runes[:limit])
}

func (runtime GoogleMediaToolRuntime) SynthesizeSpeech(
	ctx context.Context,
	text string,
	languageCode string,
	voiceName string,
) ([]byte, error) {
	if runtime.Credentials == nil {
		return nil, fmt.Errorf("Thiếu GOOGLE_APPLICATION_CREDENTIALS")
	}
	text = googleTrimRunes(text, 3000)
	if text == "" {
		return nil, fmt.Errorf("Nội dung TTS rỗng.")
	}
	languageCode = strings.TrimSpace(languageCode)
	if languageCode == "" {
		languageCode = "vi-VN"
	}
	voice := map[string]any{"languageCode": languageCode}
	if voiceName = strings.TrimSpace(voiceName); voiceName != "" {
		voice["name"] = voiceName
	}

	token, err := runtime.Credentials.Token(ctx, false)
	if err != nil {
		return nil, err
	}
	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodPost,
		googleEndpoint(runtime.TTSURL, DefaultGoogleTTSURL),
		map[string]string{
			"Authorization": "Bearer " + token,
			"Content-Type":  "application/json",
		},
		nil,
		map[string]any{
			"input": map[string]any{"text": text},
			"voice": voice,
			"audioConfig": map[string]any{
				"audioEncoding": "OGG_OPUS",
			},
		},
	)
	if err != nil {
		return nil, err
	}
	encoded := googleString(payload["audioContent"])
	if encoded == "" {
		return nil, fmt.Errorf("TTS không trả audioContent.")
	}
	audio, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return nil, fmt.Errorf("TTS audioContent base64 không hợp lệ: %w", err)
	}
	return audio, nil
}

func (runtime GoogleMediaToolRuntime) SendTTSVoice(
	ctx context.Context,
	toolContext ToolContext,
	text string,
	languageCode string,
	voiceName string,
) map[string]any {
	sender := googleVoiceSenderFromContext(runtime, toolContext)
	if sender == nil {
		return googleToolError(
			"Không có Telegram voice sender để gửi TTS.",
			"RUNTIME_NOT_WIRED",
		)
	}
	audio, err := runtime.SynthesizeSpeech(ctx, text, languageCode, voiceName)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	if err := sender(ctx, toolContext, audio, "atri-google-tts.ogg"); err != nil {
		return googleToolError("Không gửi được Telegram voice: "+err.Error(), "")
	}
	return googleToolOK(map[string]any{
		"source": "Google Cloud Text-to-Speech",
		"sent":   true,
		"bytes":  len(audio),
	})
}

func (runtime GoogleMediaToolRuntime) VisionOCR(
	ctx context.Context,
	toolContext ToolContext,
) map[string]any {
	if runtime.Credentials == nil {
		return googleMediaCredentialsError()
	}
	data, mimeType, ok := googleMediaAttachment(toolContext)
	if !ok {
		return googleToolError("Hãy gửi/reply một ảnh hoặc tài liệu ảnh để OCR.", "")
	}
	if !strings.HasPrefix(strings.ToLower(mimeType), "image/") {
		return googleToolError("Cloud Vision OCR trực tiếp chỉ nhận ảnh trong tool này.", "")
	}
	token, err := runtime.Credentials.Token(ctx, false)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodPost,
		googleEndpoint(runtime.VisionURL, DefaultGoogleVisionURL),
		map[string]string{
			"Authorization": "Bearer " + token,
			"Content-Type":  "application/json",
		},
		nil,
		map[string]any{
			"requests": []any{
				map[string]any{
					"image": map[string]any{
						"content": base64.StdEncoding.EncodeToString(data),
					},
					"features": []any{
						map[string]any{"type": "DOCUMENT_TEXT_DETECTION"},
					},
					"imageContext": map[string]any{
						"languageHints": []any{"vi", "en"},
					},
				},
			},
		},
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	responses := weatherSlice(payload["responses"])
	first := map[string]any{}
	if len(responses) > 0 {
		first = weatherMap(responses[0])
	}
	if errorObject := weatherMap(first["error"]); len(errorObject) > 0 {
		message := googleString(errorObject["message"])
		if message == "" {
			message = fmt.Sprint(errorObject)
		}
		return googleToolError(message, "")
	}
	annotation := weatherMap(first["fullTextAnnotation"])
	text := strings.TrimSpace(googleString(annotation["text"]))
	return googleToolOK(map[string]any{
		"source":    "Google Cloud Vision OCR",
		"text":      truncateRunes(text, 30000),
		"truncated": len([]rune(text)) > 30000,
		"pages":     len(weatherSlice(annotation["pages"])),
	})
}

func (runtime GoogleMediaToolRuntime) documentAIURL(
	project string,
	location string,
	processor string,
) string {
	path := "/v1/projects/" + url.PathEscape(project) +
		"/locations/" + url.PathEscape(location) +
		"/processors/" + url.PathEscape(processor) + ":process"
	if base := strings.TrimRight(strings.TrimSpace(runtime.DocumentAIBaseURL), "/"); base != "" {
		return base + path
	}
	return "https://" + location + "-documentai.googleapis.com" + path
}

func (runtime GoogleMediaToolRuntime) DocumentAI(
	ctx context.Context,
	toolContext ToolContext,
) map[string]any {
	if runtime.Credentials == nil {
		return googleMediaCredentialsError()
	}
	project, err := runtime.Credentials.ProjectID()
	if err != nil || strings.TrimSpace(project) == "" {
		return googleToolError(
			"Thiếu GOOGLE_DOCUMENT_AI_PROCESSOR_ID hoặc project.",
			"NOT_CONFIGURED",
		)
	}
	processor := runtime.setting("GOOGLE_DOCUMENT_AI_PROCESSOR_ID")
	if processor == "" {
		return googleToolError(
			"Thiếu GOOGLE_DOCUMENT_AI_PROCESSOR_ID hoặc project.",
			"NOT_CONFIGURED",
		)
	}
	location := runtime.setting("GOOGLE_DOCUMENT_AI_LOCATION")
	if location == "" {
		location = "us"
	}
	data, mimeType, ok := googleMediaAttachment(toolContext)
	if !ok {
		return googleToolError("Hãy gửi/reply PDF hoặc ảnh cần phân tích.", "")
	}

	token, err := runtime.Credentials.Token(ctx, false)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodPost,
		runtime.documentAIURL(project, location, processor),
		map[string]string{
			"Authorization": "Bearer " + token,
			"Content-Type":  "application/json",
		},
		nil,
		map[string]any{
			"rawDocument": map[string]any{
				"content":  base64.StdEncoding.EncodeToString(data),
				"mimeType": mimeType,
			},
		},
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	document := weatherMap(payload["document"])
	text := strings.TrimSpace(googleString(document["text"]))
	entities := []any{}
	for index, rawEntity := range weatherSlice(document["entities"]) {
		if index >= 100 {
			break
		}
		entity := weatherMap(rawEntity)
		entities = append(entities, map[string]any{
			"type":             entity["type"],
			"mention_text":     entity["mentionText"],
			"confidence":       entity["confidence"],
			"normalized_value": entity["normalizedValue"],
		})
	}
	return googleToolOK(map[string]any{
		"source":    "Google Document AI",
		"text":      truncateRunes(text, 30000),
		"truncated": len([]rune(text)) > 30000,
		"entities":  entities,
	})
}

func (runtime GoogleMediaToolRuntime) RegisteredTools() []RegisteredTool {
	return []RegisteredTool{
		{
			Name:        "google_tts_speak",
			Declaration: GoogleTTSDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, toolContext ToolContext, arguments map[string]any) (any, error) {
				return runtime.SendTTSVoice(
					ctx,
					toolContext,
					googleString(arguments["text"]),
					googleString(arguments["language_code"]),
					googleString(arguments["voice_name"]),
				), nil
			},
		},
		{
			Name:        "google_vision_ocr",
			Declaration: GoogleVisionOCRDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, toolContext ToolContext, _ map[string]any) (any, error) {
				return runtime.VisionOCR(ctx, toolContext), nil
			},
		},
		{
			Name:        "google_document_ai",
			Declaration: GoogleDocumentAIDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, toolContext ToolContext, _ map[string]any) (any, error) {
				return runtime.DocumentAI(ctx, toolContext), nil
			},
		},
	}
}

func RegisterGoogleMediaTools(registry *ToolRegistry, runtime GoogleMediaToolRuntime) error {
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
