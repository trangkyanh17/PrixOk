package runtimecfg

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func writeServiceAccountFile(
	t *testing.T,
	directory string,
	privateKey *rsa.PrivateKey,
	tokenURI string,
) string {
	t.Helper()
	encoded, err := x509.MarshalPKCS8PrivateKey(privateKey)
	if err != nil {
		t.Fatal(err)
	}
	privatePEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: encoded})
	payload := map[string]any{
		"type":           "service_account",
		"project_id":     "project-x",
		"private_key_id": "key-id-x",
		"private_key":    string(privatePEM),
		"client_email":   "atri@example.iam.gserviceaccount.com",
		"token_uri":      tokenURI,
	}
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "service-account.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func decodeJWTPart(t *testing.T, value string) map[string]any {
	t.Helper()
	decoded, err := base64.RawURLEncoding.DecodeString(value)
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]any
	if err := json.Unmarshal(decoded, &payload); err != nil {
		t.Fatal(err)
	}
	return payload
}

func verifyServiceAccountAssertion(
	t *testing.T,
	assertion string,
	publicKey *rsa.PublicKey,
	tokenURI string,
) {
	t.Helper()
	parts := strings.Split(assertion, ".")
	if len(parts) != 3 {
		t.Fatalf("jwt parts=%d", len(parts))
	}
	header := decodeJWTPart(t, parts[0])
	claims := decodeJWTPart(t, parts[1])
	if header["alg"] != "RS256" || header["typ"] != "JWT" || header["kid"] != "key-id-x" {
		t.Fatalf("header=%v", header)
	}
	if claims["iss"] != "atri@example.iam.gserviceaccount.com" || claims["scope"] != VertexScope || claims["aud"] != tokenURI {
		t.Fatalf("claims=%v", claims)
	}
	if claims["exp"].(float64)-claims["iat"].(float64) != 3600 {
		t.Fatalf("claims=%v", claims)
	}
	signature, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if err := rsa.VerifyPKCS1v15(publicKey, crypto.SHA256, digest[:], signature); err != nil {
		t.Fatalf("signature verify: %v", err)
	}
}

func TestServiceAccountTokenProviderRefreshesAndCaches(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	var requests atomic.Int64
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.Method != http.MethodPost || r.Header.Get("Content-Type") != "application/x-www-form-urlencoded" {
			t.Errorf("method=%s content-type=%q", r.Method, r.Header.Get("Content-Type"))
		}
		body := make([]byte, r.ContentLength)
		_, _ = r.Body.Read(body)
		values, err := url.ParseQuery(string(body))
		if err != nil {
			t.Errorf("form: %v", err)
		}
		if values.Get("grant_type") != "urn:ietf:params:oauth:grant-type:jwt-bearer" {
			t.Errorf("grant_type=%q", values.Get("grant_type"))
		}
		verifyServiceAccountAssertion(t, values.Get("assertion"), &privateKey.PublicKey, server.URL)
		_, _ = w.Write([]byte(`{"access_token":"access-one","expires_in":3600,"token_type":"Bearer"}`))
	}))
	defer server.Close()

	path := writeServiceAccountFile(t, t.TempDir(), privateKey, server.URL)
	clock := time.Date(2026, 8, 14, 1, 2, 3, 0, time.UTC)
	provider := NewServiceAccountTokenProvider(path)
	provider.Client = server.Client()
	provider.Now = func() time.Time { return clock }

	token, err := provider.Token(context.Background(), false)
	if err != nil || token != "access-one" {
		t.Fatalf("token=%q err=%v", token, err)
	}
	token, err = provider.Token(context.Background(), false)
	if err != nil || token != "access-one" {
		t.Fatalf("cached token=%q err=%v", token, err)
	}
	if requests.Load() != 1 {
		t.Fatalf("requests=%d want=1", requests.Load())
	}
	project, err := provider.ProjectID()
	if err != nil || project != "project-x" {
		t.Fatalf("project=%q err=%v", project, err)
	}
}

func TestServiceAccountTokenProviderForceRefreshAndExpiry(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	var requests atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		index := requests.Add(1)
		_, _ = w.Write([]byte(`{"access_token":"token-` + string(rune('0'+index)) + `","expires_in":120}`))
	}))
	defer server.Close()
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, server.URL)
	clock := time.Date(2026, 8, 14, 1, 0, 0, 0, time.UTC)
	provider := NewServiceAccountTokenProvider(path)
	provider.Client = server.Client()
	provider.Now = func() time.Time { return clock }
	provider.ExpirySkew = 30 * time.Second

	first, err := provider.Token(context.Background(), false)
	if err != nil || first != "token-1" {
		t.Fatalf("first=%q err=%v", first, err)
	}
	second, err := provider.Token(context.Background(), true)
	if err != nil || second != "token-2" {
		t.Fatalf("forced=%q err=%v", second, err)
	}
	clock = clock.Add(100 * time.Second)
	third, err := provider.Token(context.Background(), false)
	if err != nil || third != "token-3" {
		t.Fatalf("expired=%q err=%v", third, err)
	}
	if requests.Load() != 3 {
		t.Fatalf("requests=%d", requests.Load())
	}
}

func TestServiceAccountTokenProviderSerializesConcurrentRefresh(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	var requests atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		time.Sleep(20 * time.Millisecond)
		_, _ = w.Write([]byte(`{"access_token":"shared","expires_in":3600}`))
	}))
	defer server.Close()
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, server.URL)
	provider := NewServiceAccountTokenProvider(path)
	provider.Client = server.Client()

	var waitGroup sync.WaitGroup
	errorsByCall := make(chan error, 8)
	for index := 0; index < 8; index++ {
		waitGroup.Add(1)
		go func() {
			defer waitGroup.Done()
			token, err := provider.Token(context.Background(), false)
			if err == nil && token != "shared" {
				err = &unexpectedTokenError{token: token}
			}
			errorsByCall <- err
		}()
	}
	waitGroup.Wait()
	close(errorsByCall)
	for err := range errorsByCall {
		if err != nil {
			t.Fatal(err)
		}
	}
	if requests.Load() != 1 {
		t.Fatalf("requests=%d want=1", requests.Load())
	}
}

type unexpectedTokenError struct {
	token string
}

func (err *unexpectedTokenError) Error() string {
	return "unexpected token: " + err.token
}

func TestServiceAccountTokenProviderResetReloadsCredentialFile(t *testing.T) {
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"access_token":"token","expires_in":3600}`))
	}))
	defer server.Close()
	path := writeServiceAccountFile(t, t.TempDir(), privateKey, server.URL)
	provider := NewServiceAccountTokenProvider(path)
	provider.Client = server.Client()
	if _, err := provider.Token(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	provider.Reset()
	if provider.loaded || provider.accessToken != "" || provider.privateKey != nil || provider.projectID != "" {
		t.Fatalf("provider not reset: %+v", provider)
	}
	if _, err := provider.Token(context.Background(), false); err != nil {
		t.Fatal(err)
	}
}

func TestServiceAccountTokenProviderCredentialErrors(t *testing.T) {
	provider := NewServiceAccountTokenProvider("")
	if _, err := provider.Token(context.Background(), false); err == nil || !strings.Contains(err.Error(), "GOOGLE_APPLICATION_CREDENTIALS") {
		t.Fatalf("empty path err=%v", err)
	}
	provider = NewServiceAccountTokenProvider(filepath.Join(t.TempDir(), "missing.json"))
	if _, err := provider.Token(context.Background(), false); err == nil || !strings.Contains(err.Error(), "Không tìm thấy Vertex credential") {
		t.Fatalf("missing path err=%v", err)
	}

	badPath := filepath.Join(t.TempDir(), "bad.json")
	if err := os.WriteFile(badPath, []byte(`{"client_email":"x","private_key":"bad"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	provider = NewServiceAccountTokenProvider(badPath)
	if _, err := provider.Token(context.Background(), false); err == nil || !strings.Contains(err.Error(), "private key") {
		t.Fatalf("bad key err=%v", err)
	}
}
