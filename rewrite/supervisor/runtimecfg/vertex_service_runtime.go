package runtimecfg

import (
	"context"
	"fmt"
	"strings"
)

type VertexServiceRuntime struct {
	Credentials *ServiceAccountTokenProvider
	APIBaseURL  string
	Location    string
	Model       string
}

func NewVertexServiceRuntime(
	credentialPath string,
	location string,
	model string,
) *VertexServiceRuntime {
	return &VertexServiceRuntime{
		Credentials: NewServiceAccountTokenProvider(credentialPath),
		Location:    strings.TrimSpace(location),
		Model:       strings.TrimSpace(model),
	}
}

func (runtime *VertexServiceRuntime) normalizedLocation() string {
	location := strings.TrimSpace(runtime.Location)
	if location == "" {
		return "global"
	}
	return location
}

func (runtime *VertexServiceRuntime) GenerationURL() (string, error) {
	if runtime == nil || runtime.Credentials == nil {
		return "", fmt.Errorf("vertex service-account credentials are not configured")
	}
	projectID, err := runtime.Credentials.ProjectID()
	if err != nil {
		return "", err
	}
	model := strings.TrimSpace(runtime.Model)
	if model == "" || model == "auto" {
		return "", fmt.Errorf("vertex model must be resolved before generation")
	}
	return VertexModelURL(
		runtime.APIBaseURL,
		projectID,
		runtime.normalizedLocation(),
		model,
	), nil
}

func (runtime *VertexServiceRuntime) TokenProvider() VertexTokenProvider {
	return func(ctx context.Context, forceRefresh bool) (string, error) {
		if runtime == nil || runtime.Credentials == nil {
			return "", fmt.Errorf("vertex service-account credentials are not configured")
		}
		return runtime.Credentials.Token(ctx, forceRefresh)
	}
}

func (runtime *VertexServiceRuntime) TextRuntime(
	client HTTPDoer,
	sleep VertexSleepFunc,
	maxContinuationRounds int,
	maxEmptyTextRetries int,
) (VertexTextRuntime, error) {
	url, err := runtime.GenerationURL()
	if err != nil {
		return VertexTextRuntime{}, err
	}
	return VertexTextRuntime{
		Client:                client,
		URL:                   url,
		TokenProvider:         runtime.TokenProvider(),
		Sleep:                 sleep,
		MaxContinuationRounds: maxContinuationRounds,
		MaxEmptyTextRetries:   maxEmptyTextRetries,
	}, nil
}

func (runtime *VertexServiceRuntime) ToolRuntime(
	client HTTPDoer,
	sleep VertexSleepFunc,
	mode string,
	executor VertexToolExecutor,
) (VertexToolRuntime, error) {
	url, err := runtime.GenerationURL()
	if err != nil {
		return VertexToolRuntime{}, err
	}
	return VertexToolRuntime{
		Client:        client,
		URL:           url,
		TokenProvider: runtime.TokenProvider(),
		Sleep:         sleep,
		Mode:          mode,
		ToolExecutor:  executor,
	}, nil
}
