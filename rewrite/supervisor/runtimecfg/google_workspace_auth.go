package runtimecfg

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

const DefaultGoogleOAuthTokenURL = "https://oauth2.googleapis.com/token"

type OAuthRefreshTokenProvider struct {
	ClientID     string
	ClientSecret string
	RefreshToken string
	TokenURL     string
	Client       HTTPDoer
	Now          func() time.Time
	ExpirySkew   time.Duration

	mu          sync.Mutex
	accessToken string
	expiresAt   time.Time
}

func NewOAuthRefreshTokenProvider(
	clientID string,
	clientSecret string,
	refreshToken string,
) *OAuthRefreshTokenProvider {
	return &OAuthRefreshTokenProvider{
		ClientID:     strings.TrimSpace(clientID),
		ClientSecret: strings.TrimSpace(clientSecret),
		RefreshToken: strings.TrimSpace(refreshToken),
		TokenURL:     DefaultGoogleOAuthTokenURL,
		ExpirySkew:   30 * time.Second,
	}
}

func NewWorkspaceOAuthTokenProvider(values map[string]string) *OAuthRefreshTokenProvider {
	settings := GooglePublicToolRuntime{Values: values}
	provider := NewOAuthRefreshTokenProvider(
		settings.setting("GOOGLE_OAUTH_CLIENT_ID"),
		settings.setting("GOOGLE_OAUTH_CLIENT_SECRET"),
		settings.setting("GOOGLE_OAUTH_REFRESH_TOKEN"),
	)
	return provider
}

func (provider *OAuthRefreshTokenProvider) configured() bool {
	return provider != nil &&
		strings.TrimSpace(provider.ClientID) != "" &&
		strings.TrimSpace(provider.ClientSecret) != "" &&
		strings.TrimSpace(provider.RefreshToken) != ""
}

func (provider *OAuthRefreshTokenProvider) currentTime() time.Time {
	if provider != nil && provider.Now != nil {
		return provider.Now().UTC()
	}
	return time.Now().UTC()
}

func (provider *OAuthRefreshTokenProvider) tokenURL() string {
	if provider == nil {
		return DefaultGoogleOAuthTokenURL
	}
	value := strings.TrimSpace(provider.TokenURL)
	if value == "" {
		return DefaultGoogleOAuthTokenURL
	}
	return value
}

func (provider *OAuthRefreshTokenProvider) refreshLocked(
	ctx context.Context,
	now time.Time,
) error {
	if !provider.configured() {
		return fmt.Errorf(
			"Workspace OAuth chưa cấu hình. Cần GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET và GOOGLE_OAUTH_REFRESH_TOKEN.",
		)
	}

	form := url.Values{
		"client_id":     []string{provider.ClientID},
		"client_secret": []string{provider.ClientSecret},
		"refresh_token": []string{provider.RefreshToken},
		"grant_type":    []string{"refresh_token"},
	}
	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		provider.tokenURL(),
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
		return fmt.Errorf("làm mới Workspace OAuth token: %w", err)
	}
	defer response.Body.Close()

	raw, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return fmt.Errorf("đọc Workspace OAuth token: %w", err)
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf(
			"Workspace OAuth HTTP %d: %s",
			response.StatusCode,
			truncateRunes(strings.TrimSpace(string(raw)), 500),
		)
	}

	var payload struct {
		AccessToken string  `json:"access_token"`
		ExpiresIn   float64 `json:"expires_in"`
		TokenType   string  `json:"token_type"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return fmt.Errorf("Workspace OAuth token JSON không hợp lệ: %w", err)
	}
	payload.AccessToken = strings.TrimSpace(payload.AccessToken)
	if payload.AccessToken == "" {
		return fmt.Errorf("Workspace OAuth token response thiếu access_token")
	}
	expiresIn := payload.ExpiresIn
	if expiresIn <= 0 {
		expiresIn = 3600
	}
	provider.accessToken = payload.AccessToken
	provider.expiresAt = now.Add(time.Duration(expiresIn * float64(time.Second)))
	return nil
}

func (provider *OAuthRefreshTokenProvider) Token(
	ctx context.Context,
	forceRefresh bool,
) (string, error) {
	if provider == nil {
		return "", fmt.Errorf("Workspace OAuth provider chưa được cấu hình")
	}
	provider.mu.Lock()
	defer provider.mu.Unlock()

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

func (provider *OAuthRefreshTokenProvider) Reset() {
	if provider == nil {
		return
	}
	provider.mu.Lock()
	defer provider.mu.Unlock()
	provider.accessToken = ""
	provider.expiresAt = time.Time{}
}
