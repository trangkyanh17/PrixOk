package runtimecfg

import (
	"context"
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	DefaultGoogleDriveBaseURL    = "https://www.googleapis.com/drive/v3"
	DefaultGoogleCalendarBaseURL = "https://www.googleapis.com/calendar/v3"
	DefaultGoogleGmailBaseURL    = "https://gmail.googleapis.com/gmail/v1"
	DefaultGoogleSheetsBaseURL   = "https://sheets.googleapis.com/v4"
)

type GoogleAccessTokenProvider interface {
	Token(context.Context, bool) (string, error)
}

type GoogleWorkspaceToolRuntime struct {
	Client          HTTPDoer
	TokenProvider   GoogleAccessTokenProvider
	DriveBaseURL    string
	CalendarBaseURL string
	GmailBaseURL    string
	SheetsBaseURL   string
	Now             func() time.Time
}

func GoogleDriveSearchDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_drive_search",
		"description": "Tìm file trong Google Drive riêng của chủ bot. Chỉ dùng khi chủ bot yêu cầu.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query":       map[string]any{"type": "string"},
				"max_results": map[string]any{"type": "integer", "minimum": 1, "maximum": 20},
			},
			"required": []any{"query"},
		},
	}
}

func GoogleDriveReadDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_drive_read_text",
		"description": "Đọc nội dung văn bản của file Google Drive sau khi đã tìm thấy. Chỉ dành cho chủ bot.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"file_id":   map[string]any{"type": "string"},
				"mime_type": map[string]any{"type": "string"},
			},
			"required": []any{"file_id", "mime_type"},
		},
	}
}

func GoogleCalendarDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_calendar_events",
		"description": "Đọc Google Calendar của chủ bot. Chỉ dùng khi chủ bot yêu cầu.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query":       map[string]any{"type": "string"},
				"time_min":    map[string]any{"type": "string", "description": "RFC3339."},
				"time_max":    map[string]any{"type": "string", "description": "RFC3339."},
				"max_results": map[string]any{"type": "integer", "minimum": 1, "maximum": 20},
			},
			"required": []any{},
		},
	}
}

func GoogleGmailSearchDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_gmail_search",
		"description": "Tìm email trong Gmail riêng của chủ bot bằng cú pháp tìm kiếm Gmail.",
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

func GoogleGmailReadDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_gmail_read",
		"description": "Đọc một email Gmail bằng message_id từ google_gmail_search. Chỉ dành cho chủ bot.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"message_id": map[string]any{"type": "string"},
			},
			"required": []any{"message_id"},
		},
	}
}

func GoogleSheetsReadDeclaration() map[string]any {
	return map[string]any{
		"name":        "google_sheets_read",
		"description": "Đọc một range A1 từ Google Sheets riêng của chủ bot.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"spreadsheet_id": map[string]any{"type": "string"},
				"range":          map[string]any{"type": "string"},
			},
			"required": []any{"spreadsheet_id", "range"},
		},
	}
}

func workspaceBase(value string, fallback string) string {
	value = strings.TrimRight(strings.TrimSpace(value), "/")
	if value == "" {
		return fallback
	}
	return value
}

func (runtime GoogleWorkspaceToolRuntime) token(ctx context.Context) (string, error) {
	if runtime.TokenProvider == nil {
		return "", fmt.Errorf("Workspace OAuth chưa cấu hình")
	}
	return runtime.TokenProvider.Token(ctx, false)
}

func workspaceDriveQuery(value string) string {
	escaped := strings.TrimSpace(value)
	escaped = strings.ReplaceAll(escaped, `\`, `\\`)
	escaped = strings.ReplaceAll(escaped, `'`, `\'`)
	if escaped == "" {
		return "trashed = false"
	}
	return fmt.Sprintf("fullText contains '%s' and trashed = false", escaped)
}

func (runtime GoogleWorkspaceToolRuntime) DriveSearch(
	ctx context.Context,
	query string,
	maxResults int,
) map[string]any {
	token, err := runtime.token(ctx)
	if err != nil {
		return googleToolError(err.Error(), "NOT_CONFIGURED")
	}
	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodGet,
		workspaceBase(runtime.DriveBaseURL, DefaultGoogleDriveBaseURL)+"/files",
		map[string]string{"Authorization": "Bearer " + token},
		url.Values{
			"q":        []string{workspaceDriveQuery(query)},
			"pageSize": []string{fmt.Sprint(googleClampInt(maxResults, 1, 20, 10))},
			"orderBy":  []string{"modifiedTime desc"},
			"fields":   []string{"files(id,name,mimeType,modifiedTime,webViewLink,size)"},
		},
		nil,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	return googleToolOK(map[string]any{
		"source": "Google Drive API v3",
		"query":  query,
		"files":  weatherSlice(payload["files"]),
	})
}

func workspaceRawGet(
	ctx context.Context,
	client HTTPDoer,
	rawURL string,
	headers map[string]string,
	query url.Values,
	limit int64,
) ([]byte, error) {
	endpoint, err := url.Parse(rawURL)
	if err != nil {
		return nil, err
	}
	values := endpoint.Query()
	for key, items := range query {
		for _, item := range items {
			values.Add(key, item)
		}
	}
	endpoint.RawQuery = values.Encode()

	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return nil, err
	}
	for name, value := range headers {
		request.Header.Set(name, value)
	}
	if client == nil {
		client = http.DefaultClient
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if limit <= 0 {
		limit = 2 << 20
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, limit))
	if err != nil {
		return nil, err
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, fmt.Errorf("HTTP %d: %s", response.StatusCode, truncateRunes(strings.TrimSpace(string(raw)), 800))
	}
	return raw, nil
}

func (runtime GoogleWorkspaceToolRuntime) DriveReadText(
	ctx context.Context,
	fileID string,
	mimeType string,
) map[string]any {
	token, err := runtime.token(ctx)
	if err != nil {
		return googleToolError(err.Error(), "NOT_CONFIGURED")
	}
	fileID = strings.TrimSpace(fileID)
	mimeType = strings.TrimSpace(mimeType)
	if fileID == "" {
		return googleToolError("Thiếu file_id.", "")
	}

	base := workspaceBase(runtime.DriveBaseURL, DefaultGoogleDriveBaseURL)
	rawURL := base + "/files/" + url.PathEscape(fileID)
	params := url.Values{}
	if strings.HasPrefix(mimeType, "application/vnd.google-apps.") {
		rawURL += "/export"
		params.Set("mimeType", "text/plain")
	} else {
		params.Set("alt", "media")
	}

	raw, err := workspaceRawGet(
		ctx,
		runtime.Client,
		rawURL,
		map[string]string{"Authorization": "Bearer " + token},
		params,
		2<<20,
	)
	if err != nil {
		return googleToolError("Không đọc được file Drive: "+err.Error(), "")
	}
	text := strings.TrimSpace(string(raw))
	if text == "" {
		return googleToolError("File không có nội dung text đọc được.", "")
	}
	return googleToolOK(map[string]any{
		"source":    "Google Drive API v3",
		"file_id":   fileID,
		"content":   truncateRunes(text, 20000),
		"truncated": len([]rune(text)) > 20000,
	})
}

func (runtime GoogleWorkspaceToolRuntime) nowUTC() time.Time {
	if runtime.Now != nil {
		return runtime.Now().UTC()
	}
	return time.Now().UTC()
}

func workspaceRFC3339(value time.Time) string {
	return value.UTC().Format(time.RFC3339)
}

func (runtime GoogleWorkspaceToolRuntime) CalendarEvents(
	ctx context.Context,
	query string,
	timeMin string,
	timeMax string,
	maxResults int,
) map[string]any {
	token, err := runtime.token(ctx)
	if err != nil {
		return googleToolError(err.Error(), "NOT_CONFIGURED")
	}
	now := runtime.nowUTC()
	timeMin = strings.TrimSpace(timeMin)
	timeMax = strings.TrimSpace(timeMax)
	if timeMin == "" {
		timeMin = workspaceRFC3339(now)
	}
	if timeMax == "" {
		timeMax = workspaceRFC3339(now.Add(7 * 24 * time.Hour))
	}

	params := url.Values{
		"timeMin":      []string{timeMin},
		"timeMax":      []string{timeMax},
		"singleEvents": []string{"true"},
		"orderBy":      []string{"startTime"},
		"maxResults":   []string{fmt.Sprint(googleClampInt(maxResults, 1, 20, 10))},
	}
	if value := strings.TrimSpace(query); value != "" {
		params.Set("q", value)
	}

	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodGet,
		workspaceBase(runtime.CalendarBaseURL, DefaultGoogleCalendarBaseURL)+"/calendars/primary/events",
		map[string]string{"Authorization": "Bearer " + token},
		params,
		nil,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}

	events := []any{}
	for _, rawItem := range weatherSlice(payload["items"]) {
		item := weatherMap(rawItem)
		description := truncateRunes(googleString(item["description"]), 1000)
		events = append(events, map[string]any{
			"id":          item["id"],
			"summary":     item["summary"],
			"description": description,
			"location":    item["location"],
			"start":       item["start"],
			"end":         item["end"],
			"status":      item["status"],
			"html_link":   item["htmlLink"],
		})
	}
	return googleToolOK(map[string]any{
		"source":   "Google Calendar API v3",
		"time_min": timeMin,
		"time_max": timeMax,
		"events":   events,
	})
}

func workspaceHeaderMap(headers any) map[string]string {
	result := map[string]string{}
	for _, rawHeader := range weatherSlice(headers) {
		header := weatherMap(rawHeader)
		name := strings.ToLower(strings.TrimSpace(googleString(header["name"])))
		if name != "" {
			result[name] = googleString(header["value"])
		}
	}
	return result
}

func (runtime GoogleWorkspaceToolRuntime) GmailSearch(
	ctx context.Context,
	query string,
	maxResults int,
) map[string]any {
	token, err := runtime.token(ctx)
	if err != nil {
		return googleToolError(err.Error(), "NOT_CONFIGURED")
	}
	base := workspaceBase(runtime.GmailBaseURL, DefaultGoogleGmailBaseURL)
	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodGet,
		base+"/users/me/messages",
		map[string]string{"Authorization": "Bearer " + token},
		url.Values{
			"q":          []string{strings.TrimSpace(query)},
			"maxResults": []string{fmt.Sprint(googleClampInt(maxResults, 1, 10, 5))},
		},
		nil,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}

	messages := []any{}
	for _, rawRef := range weatherSlice(payload["messages"]) {
		ref := weatherMap(rawRef)
		messageID := googleString(ref["id"])
		if messageID == "" {
			continue
		}
		params := url.Values{"format": []string{"metadata"}}
		for _, header := range []string{"Subject", "From", "To", "Date"} {
			params.Add("metadataHeaders", header)
		}
		detail, detailErr := googleJSONRequest(
			ctx,
			runtime.Client,
			http.MethodGet,
			base+"/users/me/messages/"+url.PathEscape(messageID),
			map[string]string{"Authorization": "Bearer " + token},
			params,
			nil,
		)
		if detailErr != nil {
			return googleToolError(detailErr.Error(), "")
		}
		headers := workspaceHeaderMap(weatherMap(detail["payload"])["headers"])
		messages = append(messages, map[string]any{
			"message_id": messageID,
			"thread_id":  detail["threadId"],
			"subject":    headers["subject"],
			"from":       headers["from"],
			"to":         headers["to"],
			"date":       headers["date"],
			"snippet":    detail["snippet"],
			"label_ids":  detail["labelIds"],
		})
	}
	return googleToolOK(map[string]any{
		"source":   "Gmail API v1",
		"query":    query,
		"messages": messages,
	})
}

func workspaceGmailBodyText(payload map[string]any) string {
	if payload == nil {
		return ""
	}
	mimeType := googleString(payload["mimeType"])
	body := weatherMap(payload["body"])
	if strings.HasPrefix(mimeType, "text/plain") {
		data := googleString(body["data"])
		if data != "" {
			if decoded, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(data, "=")); err == nil {
				return string(decoded)
			}
			if decoded, err := base64.URLEncoding.DecodeString(data); err == nil {
				return string(decoded)
			}
		}
	}

	plainParts := []string{}
	fallbackParts := []string{}
	for _, rawPart := range weatherSlice(payload["parts"]) {
		part := weatherMap(rawPart)
		text := workspaceGmailBodyText(part)
		if text == "" {
			continue
		}
		if strings.HasPrefix(googleString(part["mimeType"]), "text/plain") {
			plainParts = append(plainParts, text)
		} else {
			fallbackParts = append(fallbackParts, text)
		}
	}
	if len(plainParts) > 0 {
		return strings.Join(plainParts, "\n")
	}
	return strings.Join(fallbackParts, "\n")
}

func (runtime GoogleWorkspaceToolRuntime) GmailRead(
	ctx context.Context,
	messageID string,
) map[string]any {
	token, err := runtime.token(ctx)
	if err != nil {
		return googleToolError(err.Error(), "NOT_CONFIGURED")
	}
	messageID = strings.TrimSpace(messageID)
	if messageID == "" {
		return googleToolError("Thiếu message_id.", "")
	}

	detail, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodGet,
		workspaceBase(runtime.GmailBaseURL, DefaultGoogleGmailBaseURL)+"/users/me/messages/"+url.PathEscape(messageID),
		map[string]string{"Authorization": "Bearer " + token},
		url.Values{"format": []string{"full"}},
		nil,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	payload := weatherMap(detail["payload"])
	headers := workspaceHeaderMap(payload["headers"])
	bodyText := strings.TrimSpace(workspaceGmailBodyText(payload))
	return googleToolOK(map[string]any{
		"source":     "Gmail API v1",
		"message_id": messageID,
		"subject":    headers["subject"],
		"from":       headers["from"],
		"to":         headers["to"],
		"date":       headers["date"],
		"body":       truncateRunes(bodyText, 20000),
		"truncated":  len([]rune(bodyText)) > 20000,
	})
}

func (runtime GoogleWorkspaceToolRuntime) SheetsRead(
	ctx context.Context,
	spreadsheetID string,
	rangeA1 string,
) map[string]any {
	token, err := runtime.token(ctx)
	if err != nil {
		return googleToolError(err.Error(), "NOT_CONFIGURED")
	}
	spreadsheetID = strings.TrimSpace(spreadsheetID)
	rangeA1 = strings.TrimSpace(rangeA1)
	if spreadsheetID == "" || rangeA1 == "" {
		return googleToolError("Thiếu spreadsheet_id hoặc range.", "")
	}

	rawURL := workspaceBase(runtime.SheetsBaseURL, DefaultGoogleSheetsBaseURL) +
		"/spreadsheets/" + url.PathEscape(spreadsheetID) +
		"/values/" + url.PathEscape(rangeA1)
	payload, err := googleJSONRequest(
		ctx,
		runtime.Client,
		http.MethodGet,
		rawURL,
		map[string]string{"Authorization": "Bearer " + token},
		url.Values{"majorDimension": []string{"ROWS"}},
		nil,
	)
	if err != nil {
		return googleToolError(err.Error(), "")
	}
	values := weatherSlice(payload["values"])
	truncated := len(values) > 500
	if truncated {
		values = values[:500]
	}
	rangeResult := googleString(payload["range"])
	if rangeResult == "" {
		rangeResult = rangeA1
	}
	return googleToolOK(map[string]any{
		"source":         "Google Sheets API v4",
		"spreadsheet_id": spreadsheetID,
		"range":          rangeResult,
		"values":         values,
		"truncated":      truncated,
	})
}

func (runtime GoogleWorkspaceToolRuntime) RegisteredTools() []RegisteredTool {
	return []RegisteredTool{
		{
			Name:        "google_drive_search",
			Declaration: GoogleDriveSearchDeclaration(),
			Privacy:     ToolPrivacyPrivate,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.DriveSearch(
					ctx,
					googleString(arguments["query"]),
					googleClampInt(arguments["max_results"], 1, 20, 10),
				), nil
			},
		},
		{
			Name:        "google_drive_read_text",
			Declaration: GoogleDriveReadDeclaration(),
			Privacy:     ToolPrivacyPrivate,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.DriveReadText(
					ctx,
					googleString(arguments["file_id"]),
					googleString(arguments["mime_type"]),
				), nil
			},
		},
		{
			Name:        "google_calendar_events",
			Declaration: GoogleCalendarDeclaration(),
			Privacy:     ToolPrivacyPrivate,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.CalendarEvents(
					ctx,
					googleString(arguments["query"]),
					googleString(arguments["time_min"]),
					googleString(arguments["time_max"]),
					googleClampInt(arguments["max_results"], 1, 20, 10),
				), nil
			},
		},
		{
			Name:        "google_gmail_search",
			Declaration: GoogleGmailSearchDeclaration(),
			Privacy:     ToolPrivacyPrivate,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.GmailSearch(
					ctx,
					googleString(arguments["query"]),
					googleClampInt(arguments["max_results"], 1, 10, 5),
				), nil
			},
		},
		{
			Name:        "google_gmail_read",
			Declaration: GoogleGmailReadDeclaration(),
			Privacy:     ToolPrivacyPrivate,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.GmailRead(ctx, googleString(arguments["message_id"])), nil
			},
		},
		{
			Name:        "google_sheets_read",
			Declaration: GoogleSheetsReadDeclaration(),
			Privacy:     ToolPrivacyPrivate,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.SheetsRead(
					ctx,
					googleString(arguments["spreadsheet_id"]),
					googleString(arguments["range"]),
				), nil
			},
		},
	}
}

func RegisterGoogleWorkspaceTools(registry *ToolRegistry, runtime GoogleWorkspaceToolRuntime) error {
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
