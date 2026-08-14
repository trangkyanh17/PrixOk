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
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

const VertexScope = "https://www.googleapis.com/auth/cloud-platform"
const defaultGoogleTokenURI = "https://oauth2.googleapis.com/token"

type serviceAccountFile struct {
	Type         string `json:"type"`
	ProjectID    string `json:"project_id"`
	PrivateKeyID string `json:"private_key_id"`
	PrivateKey   string `json:"private_key"`
	ClientEmail  string `json:"client_email"`
	TokenURI     string `json:"token_uri"`
}

type ServiceAccountTokenProvider struct {
	Path       string
	Scope      string
	Client     HTTPDoer
	Now        func() time.Time
	ExpirySkew time.Duration

	mu          sync.Mutex
	loaded      bool
	account     serviceAccountFile
	privateKey  *rsa.PrivateKey
	accessToken string
	expiresAt   time.Time
	projectID   string
}

func NewServiceAccountTokenProvider(path string) *ServiceAccountTokenProvider {
	return &ServiceAccountTokenProvider{
		Path:       strings.TrimSpace(path),
		Scope:      VertexScope,
		ExpirySkew: 30 * time.Second,
	}
}

func (provider *ServiceAccountTokenProvider) currentTime() time.Time {
	if provider.Now != nil {
		return provider.Now().UTC()
	}
	return time.Now().UTC()
}

func parseRSAPrivateKey(value string) (*rsa.PrivateKey, error) {
	block, _ := pem.Decode([]byte(value))
	if block == nil {
		return nil, fmt.Errorf("service-account private key is not PEM")
	}
	if key, err := x509.ParsePKCS8PrivateKey(block.Bytes); err == nil {
		if rsaKey, ok := key.(*rsa.PrivateKey); ok {
			return rsaKey, nil
		}
		return nil, fmt.Errorf("service-account private key is not RSA")
	}
	if key, err := x509.ParsePKCS1PrivateKey(block.Bytes); err == nil {
		return key, nil
	}
	return nil, fmt.Errorf("service-account private key is invalid")
}

func (provider *ServiceAccountTokenProvider) loadLocked() error {
	if provider.loaded {
		return nil
	}
	path := strings.TrimSpace(provider.Path)
	if path == "" {
		return fmt.Errorf("GOOGLE_APPLICATION_CREDENTIALS chưa được cấu hình")
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("Không tìm thấy Vertex credential: %s", path)
		}
		return fmt.Errorf("đọc Vertex credential: %w", err)
	}
	var account serviceAccountFile
	if err := json.Unmarshal(payload, &account); err != nil {
		return fmt.Errorf("Vertex credential JSON không hợp lệ: %w", err)
	}
	account.ClientEmail = strings.TrimSpace(account.ClientEmail)
	account.ProjectID = strings.TrimSpace(account.ProjectID)
	account.PrivateKeyID = strings.TrimSpace(account.PrivateKeyID)
	account.TokenURI = strings.TrimSpace(account.TokenURI)
	if account.TokenURI == "" {
		account.TokenURI = defaultGoogleTokenURI
	}
	if account.ClientEmail == "" || strings.TrimSpace(account.PrivateKey) == "" {
		return fmt.Errorf("Vertex credential thiếu client_email hoặc private_key")
	}
	privateKey, err := parseRSAPrivateKey(account.PrivateKey)
	if err != nil {
		return err
	}
	provider.account = account
	provider.privateKey = privateKey
	provider.projectID = account.ProjectID
	provider.loaded = true
	return nil
}

func jwtSegment(value any) (string, error) {
	payload, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(payload), nil
}

func (provider *ServiceAccountTokenProvider) assertionLocked(now time.Time) (string, error) {
	header := map[string]any{
		"alg": "RS256",
		"typ": "JWT",
	}
	if provider.account.PrivateKeyID != "" {
		header["kid"] = provider.account.PrivateKeyID
	}
	scope := strings.TrimSpace(provider.Scope)
	if scope == "" {
		scope = VertexScope
	}
	claims := map[string]any{
		"iss":   provider.account.ClientEmail,
		"scope": scope,
		"aud":   provider.account.TokenURI,
		"iat":   now.Unix(),
		"exp":   now.Add(time.Hour).Unix(),
	}
	headerPart, err := jwtSegment(header)
	if err != nil {
		return "", err
	}
	claimsPart, err := jwtSegment(claims)
	if err != nil {
		return "", err
	}
	unsigned := headerPart + "." + claimsPart
	digest := sha256.Sum256([]byte(unsigned))
	signature, err := rsa.SignPKCS1v15(rand.Reader, provider.privateKey, crypto.SHA256, digest[:])
	if err != nil {
		return "", err
	}
	return unsigned + "." + base64.RawURLEncoding.EncodeToString(signature), nil
}

func (provider *ServiceAccountTokenProvider) refreshLocked(ctx context.Context, now time.Time) error {
	assertion, err := provider.assertionLocked(now)
	if err != nil {
		return err
	}
	form := url.Values{}
	form.Set("grant_type", "urn:ietf:params:oauth:grant-type:jwt-bearer")
	form.Set("assertion", assertion)
	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		provider.account.TokenURI,
		strings.NewReader(form.Encode()),
	)
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	client := provider.Client
	if client == nil {
		client = http.DefaultClient
	}
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("làm mới Vertex credential: %w", err)
	}
	body, readErr := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	_ = response.Body.Close()
	if readErr != nil {
		return fmt.Errorf("đọc phản hồi Vertex token: %w", readErr)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf(
			"Vertex token HTTP %d: %s",
			response.StatusCode,
			truncateRunes(strings.TrimSpace(string(body)), 500),
		)
	}
	var payload struct {
		AccessToken string  `json:"access_token"`
		ExpiresIn   float64 `json:"expires_in"`
		TokenType   string  `json:"token_type"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		return fmt.Errorf("Vertex token JSON không hợp lệ: %w", err)
	}
	payload.AccessToken = strings.TrimSpace(payload.AccessToken)
	if payload.AccessToken == "" {
		return fmt.Errorf("Vertex token response thiếu access_token")
	}
	expiresIn := payload.ExpiresIn
	if expiresIn <= 0 {
		expiresIn = 3600
	}
	provider.accessToken = payload.AccessToken
	provider.expiresAt = now.Add(time.Duration(expiresIn * float64(time.Second)))
	return nil
}

func (provider *ServiceAccountTokenProvider) Token(ctx context.Context, forceRefresh bool) (string, error) {
	provider.mu.Lock()
	defer provider.mu.Unlock()
	if err := provider.loadLocked(); err != nil {
		return "", err
	}
	now := provider.currentTime()
	skew := provider.ExpirySkew
	if skew < 0 {
		skew = 0
	}
	if forceRefresh || provider.accessToken == "" || !provider.expiresAt.After(now.Add(skew)) {
		if err := provider.refreshLocked(ctx, now); err != nil {
			return "", err
		}
	}
	return provider.accessToken, nil
}

func (provider *ServiceAccountTokenProvider) ProjectID() (string, error) {
	provider.mu.Lock()
	defer provider.mu.Unlock()
	if err := provider.loadLocked(); err != nil {
		return "", err
	}
	return provider.projectID, nil
}

func (provider *ServiceAccountTokenProvider) Reset() {
	provider.mu.Lock()
	defer provider.mu.Unlock()
	provider.loaded = false
	provider.account = serviceAccountFile{}
	provider.privateKey = nil
	provider.accessToken = ""
	provider.expiresAt = time.Time{}
	provider.projectID = ""
}
