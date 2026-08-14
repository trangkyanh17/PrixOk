package runtimecfg

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestOAuthRefreshTokenProviderCachesAndRefreshes(t *testing.T) {
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if err := r.ParseForm(); err != nil {
			t.Fatal(err)
		}
		if r.Form.Get("client_id") != "client" ||
			r.Form.Get("client_secret") != "secret" ||
			r.Form.Get("refresh_token") != "refresh" ||
			r.Form.Get("grant_type") != "refresh_token" {
			t.Fatalf("form=%v", r.Form)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "workspace-token",
			"expires_in":   3600,
		})
	}))
	defer server.Close()

	now := time.Date(2026, 8, 14, 3, 0, 0, 0, time.UTC)
	provider := NewOAuthRefreshTokenProvider("client", "secret", "refresh")
	provider.TokenURL = server.URL
	provider.Client = server.Client()
	provider.Now = func() time.Time { return now }

	first, err := provider.Token(context.Background(), false)
	if err != nil || first != "workspace-token" {
		t.Fatalf("first=%q err=%v", first, err)
	}
	second, err := provider.Token(context.Background(), false)
	if err != nil || second != "workspace-token" {
		t.Fatalf("second=%q err=%v", second, err)
	}
	if requests != 1 {
		t.Fatalf("requests=%d", requests)
	}

	if _, err := provider.Token(context.Background(), true); err != nil {
		t.Fatal(err)
	}
	if requests != 2 {
		t.Fatalf("force refresh requests=%d", requests)
	}

	provider.Reset()
	if _, err := provider.Token(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	if requests != 3 {
		t.Fatalf("reset requests=%d", requests)
	}
}

func TestWorkspaceOAuthProviderValidationAndFactory(t *testing.T) {
	missing := NewWorkspaceOAuthTokenProvider(nil)
	if _, err := missing.Token(context.Background(), false); err == nil {
		t.Fatal("missing OAuth config should fail")
	}

	provider := NewWorkspaceOAuthTokenProvider(map[string]string{
		"GOOGLE_OAUTH_CLIENT_ID":     "client",
		"GOOGLE_OAUTH_CLIENT_SECRET": "secret",
		"GOOGLE_OAUTH_REFRESH_TOKEN": "refresh",
	})
	if !provider.configured() {
		t.Fatalf("provider=%+v", provider)
	}
}
