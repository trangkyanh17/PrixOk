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

func decodeJWTClaimsForTest(t *testing.T, assertion string) map[string]any {
	t.Helper()
	parts := strings.Split(assertion, ".")
	if len(parts) != 3 {
		t.Fatalf("jwt parts=%d", len(parts))
	}
	raw, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		t.Fatal(err)
	}
	var claims map[string]any
	if err := json.Unmarshal(raw, &claims); err != nil {
		t.Fatal(err)
	}
	return claims
}

func TestWorkspaceServiceAccountTokenProviderUsesScopesAndSubject(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	requests := 0
	tokenServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if err := r.ParseForm(); err != nil {
			t.Fatal(err)
		}
		if r.Form.Get("grant_type") != "urn:ietf:params:oauth:grant-type:jwt-bearer" {
			t.Fatalf("grant=%q", r.Form.Get("grant_type"))
		}
		claims := decodeJWTClaimsForTest(t, r.Form.Get("assertion"))
		if claims["sub"] != "owner@example.com" {
			t.Fatalf("sub=%v", claims["sub"])
		}
		scope := claims["scope"].(string)
		for _, required := range GoogleWorkspaceScopes {
			if !strings.Contains(scope, required) {
				t.Fatalf("scope missing %s: %q", required, scope)
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "workspace-sa-token",
			"expires_in":   3600,
		})
	}))
	defer tokenServer.Close()

	path := writeServiceAccountFile(t, t.TempDir(), privateKey, tokenServer.URL)
	provider := NewWorkspaceServiceAccountTokenProvider(path, "owner@example.com")
	provider.Client = tokenServer.Client()

	first, err := provider.Token(context.Background(), false)
	if err != nil || first != "workspace-sa-token" {
		t.Fatalf("first=%q err=%v", first, err)
	}
	second, err := provider.Token(context.Background(), false)
	if err != nil || second != "workspace-sa-token" {
		t.Fatalf("second=%q err=%v", second, err)
	}
	if requests != 1 {
		t.Fatalf("requests=%d", requests)
	}

	provider.Reset()
	if _, err := provider.Token(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	if requests != 2 {
		t.Fatalf("reset requests=%d", requests)
	}
}

func TestWorkspaceServiceAccountOmitsSubjectWhenUnset(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	tokenServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if err := r.ParseForm(); err != nil {
			t.Fatal(err)
		}
		claims := decodeJWTClaimsForTest(t, r.Form.Get("assertion"))
		if _, ok := claims["sub"]; ok {
			t.Fatalf("unexpected sub=%v", claims["sub"])
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "token",
			"expires_in":   3600,
		})
	}))
	defer tokenServer.Close()

	path := writeServiceAccountFile(t, t.TempDir(), privateKey, tokenServer.URL)
	provider := NewWorkspaceServiceAccountTokenProvider(path, "")
	provider.Client = tokenServer.Client()
	if _, err := provider.Token(context.Background(), false); err != nil {
		t.Fatal(err)
	}
}

func TestNewGoogleWorkspaceTokenProviderPrefersOAuth(t *testing.T) {
	provider, err := NewGoogleWorkspaceTokenProvider(map[string]string{
		"GOOGLE_OAUTH_CLIENT_ID":           "client",
		"GOOGLE_OAUTH_CLIENT_SECRET":       "secret",
		"GOOGLE_OAUTH_REFRESH_TOKEN":       "refresh",
		"GOOGLE_WORKSPACE_SERVICE_ACCOUNT": "true",
		"GOOGLE_APPLICATION_CREDENTIALS":   "/tmp/ignored.json",
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := provider.(*OAuthRefreshTokenProvider); !ok {
		t.Fatalf("provider=%T", provider)
	}
}

func TestNewGoogleWorkspaceTokenProviderServiceAccountAndValidation(t *testing.T) {
	provider, err := NewGoogleWorkspaceTokenProvider(map[string]string{
		"GOOGLE_WORKSPACE_SERVICE_ACCOUNT": "yes",
		"GOOGLE_APPLICATION_CREDENTIALS":   "/tmp/workspace.json",
		"GOOGLE_WORKSPACE_SUBJECT":         "owner@example.com",
	})
	if err != nil {
		t.Fatal(err)
	}
	serviceAccount, ok := provider.(*WorkspaceServiceAccountTokenProvider)
	if !ok {
		t.Fatalf("provider=%T", provider)
	}
	if serviceAccount.Path != "/tmp/workspace.json" || serviceAccount.Subject != "owner@example.com" {
		t.Fatalf("provider=%+v", serviceAccount)
	}

	if _, err := NewGoogleWorkspaceTokenProvider(map[string]string{
		"GOOGLE_WORKSPACE_SERVICE_ACCOUNT": "true",
	}); err == nil {
		t.Fatal("missing service-account path should fail")
	}
	if _, err := NewGoogleWorkspaceTokenProvider(nil); err == nil {
		t.Fatal("missing workspace auth should fail")
	}
}
