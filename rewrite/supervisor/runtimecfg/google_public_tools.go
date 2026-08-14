package runtimecfg

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
)

const (
	DefaultGoogleYouTubeSearchURL = "https://www.googleapis.com/youtube/v3/search"
	DefaultGoogleSafeBrowsingURL  = "https://safebrowsing.googleapis.com/v5/urls:search"
	DefaultGoogleBooksURL         = "https://www.googleapis.com/books/v1/volumes"
)

type GooglePublicToolRuntime struct {
	Client           HTTPDoer
	Values           map[string]string
	YouTubeSearchURL string
	SafeBrowsingURL  string
	BooksURL         string
}

func googleToolError(message string, code string) map[string]any {
	result := map[string]any{"ok": false, "error": message}
	if strings.TrimSpace(code) != "" {
		result["code"] = code
	}
	return result
}

func googleToolOK(values map[string]any) map[string]any {
	result := map[string]any{"ok": true}
	for key, value := range values {
		result[key] = value
	}
	return result
}

func (runtime GooglePublicToolRuntime) setting(names ...string) string {
	for _, name := range names {
		if runtime.Values == nil {
			continue
		}
		if value := strings.TrimSpace(runtime.Values[name]); value != "" {
			return value
		}
	}
	return ""
}

func googleClampInt(value any, minimum int, maximum int, fallback int) int {
	parsed, ok := weatherInt(value)
	if !ok {
		parsed = fallback
	}
	if parsed < minimum {
		return minimum
	}
	if parsed > maximum {
		return maximum
	}
	return parsed
}

func googleStringSlice(value any) []string {
	result := []string{}
	switch items := value.(type) {
	case []string:
		for _, item := range items {
			result = append(result, item)
		}
	case []any:
		for _, item := range items {
			result = append(result, fmt.Sprint(item))
		}
	}
	return result
}

func googleEndpoint(value string, fallback string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return fallback
	}
	return value
}

func googleJSONRequest(
	ctx context.Context,
	client HTTPDoer,
	method string,
	rawURL string,
	headers map[string]string,
	query url.Values,
	body any,
) (map[string]any, error) {
	endpoint, err := url.Parse(rawURL)
	if err != nil {
		return nil, fmt.Errorf("invalid Google endpoint: %w", err)
	}
	values := endpoint.Query()
	for key, items := range query {
		for _, item := range items {
			values.Add(key, item)
		}
	}
	endpoint.RawQuery = values.Encode()

	var bodyReader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		bodyReader = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, endpoint.String(), bodyReader)
	if err != nil {
		return nil, err
	}
	for name, value := range headers {
		request.Header.Set(name, value)
	}
	if body != nil && request.Header.Get("Content-Type") == "" {
		request.Header.Set("Content-Type", "application/json")
	}
	if client == nil {
		client = http.DefaultClient
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, fmt.Errorf("Network error: %T", err)
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	if response.StatusCode >= 400 {
		detail := strings.TrimSpace(string(raw))
		var errorPayload map[string]any
		if json.Unmarshal(raw, &errorPayload) == nil {
			if errorObject, ok := errorPayload["error"].(map[string]any); ok {
				if value := strings.TrimSpace(fmt.Sprint(errorObject["message"])); value != "" && value != "<nil>" {
					detail = value
				} else if value := strings.TrimSpace(fmt.Sprint(errorObject["status"])); value != "" && value != "<nil>" {
					detail = value
				}
			}
		}
		return nil, fmt.Errorf("HTTP %d: %s", response.StatusCode, detail)
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, fmt.Errorf("API returned invalid JSON")
	}
	return payload, nil
}

func GoogleYouTubeSearchDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_youtube_search",
		"description": "Tìm video công khai trên YouTube. Dùng khi người dùng yêu cầu tìm video, kênh hoặc nội dung YouTube.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query":       map[string]any{"type": "string", "description": "Từ khóa tìm kiếm YouTube."},
				"max_results": map[string]any{"type": "integer", "minimum": 1, "maximum": 10},
				"region_code": map[string]any{"type": "string", "description": "Mã quốc gia ISO-3166-1 alpha-2, ví dụ VN."},
			},
			"required": []any{"query"},
		},
	}
}

func GoogleSafeBrowsingDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_safe_browsing",
		"description": "Kiểm tra URL có nằm trong danh sách URL nguy hiểm của Google Safe Browsing hay không.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"urls": map[string]any{
					"type":        "array",
					"items":       map[string]any{"type": "string"},
					"description": "Danh sách URL http/https cần kiểm tra.",
				},
			},
			"required": []any{"urls"},
		},
	}
}

func GoogleBooksSearchDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_books_search",
		"description": "Tìm sách, tác giả, ISBN và metadata sách bằng Google Books.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query":       map[string]any{"type": "string"},
				"max_results": map[string]any{"type": "integer", "minimum": 1, "maximum": 10},
			},
			"required": []any{"query"},
		},
	}
}

func (runtime GooglePublicToolRuntime) YouTubeSearch(
	ctx context.Context,
	query string,
	maxResults int,
	regionCode string,
) map[string]any {
	key := runtime.setting("YOUTUBE_API_KEY", "GOOGLE_API_KEY")
	if key == "" {
		return googleToolError("Thiếu YOUTUBE_API_KEY hoặc GOOGLE_API_KEY.", "NOT_CONFIGURED")
	}
	query = strings.TrimSpace(query)
	if query == "" {
		return googleToolError("Query YouTube rỗng.", "")
	}
	regionCode = strings.ToUpper(strings.TrimSpace(regionCode))
	if regionCode == "" {
		regionCode = "VN"
	}
	if len(regionCode) > 2 {
		regionCode = regionCode[:2]
	}
	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodGet,
		googleEndpoint(runtime.YouTubeSearchURL, DefaultGoogleYouTubeSearchURL),
		nil,
		url.Values{
			"part":       []string{"snippet"},
			"q":          []string{query},
			"type":       []string{"video"},
			"maxResults": []string{strconv.Itoa(googleClampInt(maxResults, 1, 10, 5))},
			"regionCode": []string{regionCode},
			"safeSearch": []string{"moderate"},
			"key":        []string{key},
		},
		nil,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	results := []any{}
	for _, rawItem := range weatherSlice(payload["items"]) {
		item := weatherMap(rawItem)
		identity := weatherMap(item["id"])
		snippet := weatherMap(item["snippet"])
		videoID := strings.TrimSpace(fmt.Sprint(identity["videoId"]))
		if videoID == "" || videoID == "<nil>" {
			continue
		}
		description := fmt.Sprint(snippet["description"])
		if description == "<nil>" {
			description = ""
		}
		if len([]rune(description)) > 400 {
			description = string([]rune(description)[:400])
		}
		results = append(results, map[string]any{
			"video_id":     videoID,
			"title":        snippet["title"],
			"channel":      snippet["channelTitle"],
			"published_at": snippet["publishedAt"],
			"description":  description,
			"url":          "https://www.youtube.com/watch?v=" + videoID,
		})
	}
	return googleToolOK(map[string]any{
		"source":  "YouTube Data API v3",
		"query":   query,
		"results": results,
	})
}

func (runtime GooglePublicToolRuntime) SafeBrowsing(ctx context.Context, urls []string) map[string]any {
	key := runtime.setting("SAFE_BROWSING_API_KEY", "GOOGLE_API_KEY")
	if key == "" {
		return googleToolError("Thiếu SAFE_BROWSING_API_KEY hoặc GOOGLE_API_KEY.", "NOT_CONFIGURED")
	}
	cleaned := []string{}
	for _, raw := range urls {
		value := strings.TrimSpace(raw)
		if strings.HasPrefix(value, "http://") || strings.HasPrefix(value, "https://") {
			cleaned = append(cleaned, value)
		}
		if len(cleaned) == 50 {
			break
		}
	}
	if len(cleaned) == 0 {
		return googleToolError("Không có URL http/https hợp lệ.", "")
	}
	query := url.Values{"key": []string{key}}
	for _, value := range cleaned {
		query.Add("urls[]", value)
	}
	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodGet,
		googleEndpoint(runtime.SafeBrowsingURL, DefaultGoogleSafeBrowsingURL),
		map[string]string{"Accept": "application/json"},
		query,
		nil,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	threats := weatherSlice(payload["threats"])
	return googleToolOK(map[string]any{
		"source":         "Google Safe Browsing v5",
		"checked_urls":   cleaned,
		"unsafe":         len(threats) > 0,
		"threats":        threats,
		"cache_duration": payload["cacheDuration"],
	})
}

func (runtime GooglePublicToolRuntime) BooksSearch(ctx context.Context, query string, maxResults int) map[string]any {
	query = strings.TrimSpace(query)
	if query == "" {
		return googleToolError("Query sách rỗng.", "")
	}
	params := url.Values{
		"q":          []string{query},
		"maxResults": []string{strconv.Itoa(googleClampInt(maxResults, 1, 10, 5))},
		"printType":  []string{"books"},
	}
	if key := runtime.setting("GOOGLE_API_KEY"); key != "" {
		params.Set("key", key)
	}
	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodGet,
		googleEndpoint(runtime.BooksURL, DefaultGoogleBooksURL),
		nil,
		params,
		nil,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	results := []any{}
	for _, rawItem := range weatherSlice(payload["items"]) {
		item := weatherMap(rawItem)
		info := weatherMap(item["volumeInfo"])
		description := fmt.Sprint(info["description"])
		if description == "<nil>" {
			description = ""
		}
		if len([]rune(description)) > 600 {
			description = string([]rune(description)[:600])
		}
		isbn := []any{}
		for _, rawIdentifier := range weatherSlice(info["industryIdentifiers"]) {
			identifier := weatherMap(rawIdentifier)
			if value := identifier["identifier"]; value != nil {
				isbn = append(isbn, value)
			}
		}
		results = append(results, map[string]any{
			"id":             item["id"],
			"title":          info["title"],
			"authors":        info["authors"],
			"publisher":      info["publisher"],
			"published_date": info["publishedDate"],
			"description":    description,
			"isbn":           isbn,
			"info_link":      info["infoLink"],
		})
	}
	return googleToolOK(map[string]any{
		"source":  "Google Books API",
		"query":   query,
		"results": results,
	})
}

func (runtime GooglePublicToolRuntime) RegisteredTools() []RegisteredTool {
	return []RegisteredTool{
		{
			Name:        "google_youtube_search",
			Declaration: GoogleYouTubeSearchDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.YouTubeSearch(
					ctx,
					fmt.Sprint(arguments["query"]),
					googleClampInt(arguments["max_results"], 1, 10, 5),
					fmt.Sprint(arguments["region_code"]),
				), nil
			},
		},
		{
			Name:        "google_safe_browsing",
			Declaration: GoogleSafeBrowsingDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.SafeBrowsing(ctx, googleStringSlice(arguments["urls"])), nil
			},
		},
		{
			Name:        "google_books_search",
			Declaration: GoogleBooksSearchDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.BooksSearch(
					ctx,
					fmt.Sprint(arguments["query"]),
					googleClampInt(arguments["max_results"], 1, 10, 5),
				), nil
			},
		},
	}
}

func RegisterGooglePublicTools(registry *ToolRegistry, runtime GooglePublicToolRuntime) error {
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
