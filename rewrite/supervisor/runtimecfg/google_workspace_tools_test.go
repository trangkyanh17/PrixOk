package runtimecfg

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type staticGoogleTokenProvider struct {
	token string
	err   error
	calls int
}

func (provider *staticGoogleTokenProvider) Token(context.Context, bool) (string, error) {
	provider.calls++
	return provider.token, provider.err
}

func TestGoogleWorkspaceDriveCalendarGmailAndSheets(t *testing.T) {
	bodyData := base64.RawURLEncoding.EncodeToString([]byte("Nội dung email"))
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer workspace-token" {
			t.Fatalf("authorization=%q path=%s", r.Header.Get("Authorization"), r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")

		switch {
		case r.URL.Path == "/drive/files" && r.URL.Query().Get("alt") == "":
			if !strings.Contains(r.URL.Query().Get("q"), "fullText contains") ||
				r.URL.Query().Get("pageSize") != "20" ||
				r.URL.Query().Get("orderBy") != "modifiedTime desc" {
				t.Fatalf("drive search query=%v", r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"files": []any{
					map[string]any{
						"id":           "file-1",
						"name":         "Notes",
						"mimeType":     "application/vnd.google-apps.document",
						"modifiedTime": "2026-08-14T00:00:00Z",
						"webViewLink":  "https://drive.example/file-1",
					},
				},
			})
		case r.URL.Path == "/drive/files/file-1/export":
			if r.URL.Query().Get("mimeType") != "text/plain" {
				t.Fatalf("drive export query=%v", r.URL.Query())
			}
			w.Header().Set("Content-Type", "text/plain")
			_, _ = w.Write([]byte("Drive text"))
		case r.URL.Path == "/calendar/calendars/primary/events":
			if r.URL.Query().Get("singleEvents") != "true" ||
				r.URL.Query().Get("orderBy") != "startTime" ||
				r.URL.Query().Get("maxResults") != "10" {
				t.Fatalf("calendar query=%v", r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"items": []any{
					map[string]any{
						"id":          "event-1",
						"summary":     "Meeting",
						"description": "Discuss",
						"location":    "Office",
						"start":       map[string]any{"dateTime": "2026-08-15T03:00:00Z"},
						"end":         map[string]any{"dateTime": "2026-08-15T04:00:00Z"},
						"status":      "confirmed",
						"htmlLink":    "https://calendar.example/event-1",
					},
				},
			})
		case r.URL.Path == "/gmail/users/me/messages" && r.URL.Query().Get("format") == "":
			if r.URL.Query().Get("q") != "from:test@example.com" ||
				r.URL.Query().Get("maxResults") != "5" {
				t.Fatalf("gmail search query=%v", r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"messages": []any{map[string]any{"id": "msg-1"}},
			})
		case r.URL.Path == "/gmail/users/me/messages/msg-1" && r.URL.Query().Get("format") == "metadata":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id":       "msg-1",
				"threadId": "thread-1",
				"snippet":  "preview",
				"labelIds": []any{"INBOX"},
				"payload": map[string]any{
					"headers": []any{
						map[string]any{"name": "Subject", "value": "Hello"},
						map[string]any{"name": "From", "value": "test@example.com"},
						map[string]any{"name": "To", "value": "prix@example.com"},
						map[string]any{"name": "Date", "value": "Thu, 14 Aug 2026 10:00:00 +0700"},
					},
				},
			})
		case r.URL.Path == "/gmail/users/me/messages/msg-1" && r.URL.Query().Get("format") == "full":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id": "msg-1",
				"payload": map[string]any{
					"mimeType": "multipart/alternative",
					"headers": []any{
						map[string]any{"name": "Subject", "value": "Hello"},
						map[string]any{"name": "From", "value": "test@example.com"},
						map[string]any{"name": "To", "value": "prix@example.com"},
						map[string]any{"name": "Date", "value": "Thu, 14 Aug 2026 10:00:00 +0700"},
					},
					"parts": []any{
						map[string]any{
							"mimeType": "text/plain",
							"body":     map[string]any{"data": bodyData},
						},
					},
				},
			})
		case strings.HasPrefix(r.URL.Path, "/sheets/spreadsheets/sheet-1/values/"):
			if r.URL.Query().Get("majorDimension") != "ROWS" {
				t.Fatalf("sheets query=%v", r.URL.Query())
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"range":  "Sheet1!A1:B2",
				"values": []any{[]any{"A", "B"}, []any{"1", "2"}},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	provider := &staticGoogleTokenProvider{token: "workspace-token"}
	now := time.Date(2026, 8, 14, 3, 0, 0, 0, time.UTC)
	runtime := GoogleWorkspaceToolRuntime{
		Client:          server.Client(),
		TokenProvider:   provider,
		DriveBaseURL:    server.URL + "/drive",
		CalendarBaseURL: server.URL + "/calendar",
		GmailBaseURL:    server.URL + "/gmail",
		SheetsBaseURL:   server.URL + "/sheets",
		Now:             func() time.Time { return now },
	}

	drive := runtime.DriveSearch(context.Background(), "notes", 99)
	if drive["ok"] != true || len(drive["files"].([]any)) != 1 {
		t.Fatalf("drive=%v", drive)
	}

	driveText := runtime.DriveReadText(
		context.Background(),
		"file-1",
		"application/vnd.google-apps.document",
	)
	if driveText["ok"] != true || driveText["content"] != "Drive text" {
		t.Fatalf("drive text=%v", driveText)
	}

	calendar := runtime.CalendarEvents(context.Background(), "", "", "", 10)
	if calendar["ok"] != true || len(calendar["events"].([]any)) != 1 {
		t.Fatalf("calendar=%v", calendar)
	}
	if calendar["time_min"] != "2026-08-14T03:00:00Z" ||
		calendar["time_max"] != "2026-08-21T03:00:00Z" {
		t.Fatalf("calendar window=%v", calendar)
	}

	gmail := runtime.GmailSearch(context.Background(), "from:test@example.com", 5)
	if gmail["ok"] != true || len(gmail["messages"].([]any)) != 1 {
		t.Fatalf("gmail=%v", gmail)
	}
	message := gmail["messages"].([]any)[0].(map[string]any)
	if message["subject"] != "Hello" || message["from"] != "test@example.com" {
		t.Fatalf("message=%v", message)
	}

	read := runtime.GmailRead(context.Background(), "msg-1")
	if read["ok"] != true || read["body"] != "Nội dung email" {
		t.Fatalf("read=%v", read)
	}

	sheets := runtime.SheetsRead(context.Background(), "sheet-1", "Sheet1!A1:B2")
	if sheets["ok"] != true || len(sheets["values"].([]any)) != 2 {
		t.Fatalf("sheets=%v", sheets)
	}
	if provider.calls != 6 {
		t.Fatalf("token calls=%d", provider.calls)
	}
}

func TestGoogleWorkspacePrivateRegistryGate(t *testing.T) {
	provider := &staticGoogleTokenProvider{token: "token"}
	registry := NewToolRegistry()
	if err := RegisterGoogleWorkspaceTools(
		registry,
		GoogleWorkspaceToolRuntime{TokenProvider: provider},
	); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{
		"google_drive_search",
		"google_drive_read_text",
		"google_calendar_events",
		"google_gmail_search",
		"google_gmail_read",
		"google_sheets_read",
	} {
		if !registry.Has(name) {
			t.Fatalf("missing tool %s", name)
		}
	}
	if declarations := registry.Declarations("chat", false); len(declarations) != 0 {
		t.Fatalf("public declarations leaked private tools=%v", declarations)
	}
	if declarations := registry.Declarations("chat", true); len(declarations) != 6 {
		t.Fatalf("private declarations=%v", declarations)
	}

	blocked := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"google_gmail_read",
		map[string]any{"message_id": "msg"},
		false,
	).(map[string]any)
	if blocked["ok"] != false {
		t.Fatalf("blocked=%v", blocked)
	}
	if provider.calls != 0 {
		t.Fatalf("private gate called provider %d times", provider.calls)
	}
}

func TestWorkspaceDriveQueryEscapesUserText(t *testing.T) {
	query := workspaceDriveQuery(`Prix's \ notes`)
	if !strings.Contains(query, `Prix\'s`) || !strings.Contains(query, `\\`) {
		t.Fatalf("query=%q", query)
	}
	if workspaceDriveQuery("") != "trashed = false" {
		t.Fatalf("empty=%q", workspaceDriveQuery(""))
	}
}
