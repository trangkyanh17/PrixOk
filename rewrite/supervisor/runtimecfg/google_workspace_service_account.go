package runtimecfg

import (
	"context"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

var GoogleWorkspaceScopes = []string{
	"https://www.googleapis.com/auth/drive.readonly",
	"https://www.googleapis.com/auth/calendar.readonly",
	"https://www.googleapis.com/auth/gmail.readonly",
	"https://www.googleapis.com/auth/spreadsheets.readonly",
}

type WorkspaceServiceAccountTokenProvider struct {
	Path       string
	Subject    string
	Scopes     []string
	Client     HTTPDoer
	Now        func() time.Time
	ExpirySkew time.Duration

	mu          sync.Mutex
	loaded      bool
	account     serviceAccountFile
	privateKey  *rsa.PrivateKey
	accessToken string
	expiresAt   time.Time
}

func NewWorkspaceServiceAccountTokenProvider(
	path string,
	subject string,
) *WorkspaceServiceAccountTokenProvider {
	return &WorkspaceServiceAccountTokenProvider{
		Path:       strings.TrimSpace(path),
		Subject:    strings.TrimSpace(subject),
		Scopes:     append([]string(nil), GoogleWorkspaceScopes...),
		ExpirySkew: 30 * time.Second,
	}
}

func (provider *WorkspaceServiceAccountTokenProvider) currentTime() time.Time {
	if provider != nil && provider.Now != nil {
		return provider.Now().UTC()
	}
	return time.Now().UTC()
}

func (provider *WorkspaceServiceAccountTokenProvider) loadLocked() error {
	if provider.loaded {
		return nil
	}
	path := strings.TrimSpace(provider.Path)
	if path == "" {
		return fmt.Errorf("Workspace service account chưa cấu hình")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("Không tìm thấy Workspace service account: %s", path)
		}
		return fmt.Errorf("đọc Workspace service account: %w", err)
	}
	var account serviceAccountFile
	if err := json.Unmarshal(raw, &account); err != nil {
		return fmt.Errorf("Workspace service-account JSON không hợp lệ: %w", err)
	}
	account.ClientEmail = strings.TrimSpace(account.ClientEmail)
	account.PrivateKeyID = strings.TrimSpace(account.PrivateKeyID)
	account.TokenURI = strings.TrimSpace(account.TokenURI)
	if account.TokenURI == "" {
		account.TokenURI = defaultGoogleTokenURI
	}
	if account.ClientEmail == "" || strings.TrimSpace(account.PrivateKey) == "" {
		return fmt.Errorf("Workspace service account thiếu client_email hoặc private_key")
	}
	privateKey, err := parseRSAPrivateKey(account.PrivateKey)
	if err != nil {
		return err
	}
	provider.account = account
	provider.privateKey = privateKey
	provider.loaded = true
	return nil
}

func (provider *WorkspaceServiceAccountTokenProvider) assertionLocked(
	now time.Time,
) (string, error) {
	header := map[string]any{
		"alg": "RS256",
		"typ": "JWT",
	}
	if provider.account.PrivateKeyID != "" {
		header["kid"] = provider.account.PrivateKeyID
	}

	scopes := provider.Scopes
	if len(scopes) == 0 {
		scopes = GoogleWorkspaceScopes
	}
	cleanScopes := make([]string, 0, len(scopes))
	for _, scope := range scopes {
		if value := strings.TrimSpace(scope); value != "" {
			cleanScopes = append(cleanScopes, value)
		}
	}
	if len(cleanScopes) == 0 {
		return "", fmt.Errorf("Workspace service account thiếu OAuth scopes")
	}

	claims := map[string]any{
		"iss":   provider.account.ClientEmail,
		"scope": strings.Join(cleanScopes, " "),
		"aud":   provider.account.TokenURI,
		"iat":   now.Unix(),
		"exp":   now.Add(time.Hour).Unix(),
	}
	if subject := strings.TrimSpace(provider.Subject); subject != "" {
		claims["sub"] = subject
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
	signature, err := rsa.SignPKCS1v15(
		rand.Reader,
		provider.privateKey,
		crypto.SHA256,
		digest[:],
	)
	if err != nil {
		return "", err
	}
	return unsigned + "." + base64.RawURLEncoding.EncodeToString(signature), nil
}

func (provider *WorkspaceServiceAccountTokenProvider) refreshLocked(
	ctx context.Context,
	now time.Time,
) error {
	assertion, err := provider.assertionLocked(now)
	if err != nil {
		return err
	}
	form := url.Values{
		"grant_type": []string{"urn:ietf:params:oauth:grant-type:jwt-bearer"},
		"assertion":  []string{assertion},
	}
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
		return fmt.Errorf("làm mới Workspace service-account token: %w", err)
	}
	defer response.Body.Close()

	raw, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return fmt.Errorf("đọc Workspace service-account token: %w", err)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf(
			"Workspace service-account HTTP %d: %s",
			response.StatusCode,
			truncateRunes(strings.TrimSpace(string(raw)), 500),
		)
	}

	var payload struct {
		AccessToken string  `json:"access_token"`
		ExpiresIn   float64 `json:"expires_in"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return fmt.Errorf("Workspace service-account token JSON không hợp lệ: %w", err)
	}
	payload.AccessToken = strings.TrimSpace(payload.AccessToken)
	if payload.AccessToken == "" {
		return fmt.Errorf("Workspace service-account token response thiếu access_token")
	}
	expiresIn := payload.ExpiresIn
	if expiresIn <= 0 {
		expiresIn = 3600
	}
	provider.accessToken = payload.AccessToken
	provider.expiresAt = now.Add(time.Duration(expiresIn * float64(time.Second)))
	return nil
}

func (provider *WorkspaceServiceAccountTokenProvider) Token(
	ctx context.Context,
	forceRefresh bool,
) (string, error) {
	if provider == nil {
		return "", fmt.Errorf("Workspace service account chưa cấu hình")
	}
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
	if forceRefresh ||
		provider.accessToken == "" ||
		!provider.expiresAt.After(now.Add(skew)) {
		if err := provider.refreshLocked(ctx, now); err != nil {
			return "", err
		}
	}
	return provider.accessToken, nil
}

func (provider *WorkspaceServiceAccountTokenProvider) Reset() {
	if provider == nil {
		return
	}
	provider.mu.Lock()
	defer provider.mu.Unlock()
	provider.accessToken = ""
	provider.expiresAt = time.Time{}
}

func NewGoogleWorkspaceTokenProvider(
	values map[string]string,
) (GoogleAccessTokenProvider, error) {
	settings := GooglePublicToolRuntime{Values: values}
	clientID := settings.setting("GOOGLE_OAUTH_CLIENT_ID")
	clientSecret := settings.setting("GOOGLE_OAUTH_CLIENT_SECRET")
	refreshToken := settings.setting("GOOGLE_OAUTH_REFRESH_TOKEN")
	if clientID != "" && clientSecret != "" && refreshToken != "" {
		return NewOAuthRefreshTokenProvider(
			clientID,
			clientSecret,
			refreshToken,
		), nil
	}

	if googleBoolSetting(settings.setting("GOOGLE_WORKSPACE_SERVICE_ACCOUNT")) {
		path := settings.setting("GOOGLE_APPLICATION_CREDENTIALS")
		if path == "" {
			return nil, fmt.Errorf("Workspace service account chưa cấu hình")
		}
		return NewWorkspaceServiceAccountTokenProvider(
			path,
			settings.setting("GOOGLE_WORKSPACE_SUBJECT"),
		), nil
	}

	return nil, fmt.Errorf(
		"Workspace OAuth chưa cấu hình. Cần GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET và GOOGLE_OAUTH_REFRESH_TOKEN.",
	)
}
