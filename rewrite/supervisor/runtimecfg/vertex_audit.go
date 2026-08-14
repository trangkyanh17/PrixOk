package runtimecfg

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

const DefaultVertexAPIBaseURL = "https://aiplatform.googleapis.com/v1"

type VertexAuditCredentials struct {
	Token    string
	Project  string
	Location string
}

func VertexModelURL(baseURL, project, location, model string) string {
	baseURL = strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if baseURL == "" {
		baseURL = DefaultVertexAPIBaseURL
	}
	location = strings.TrimSpace(location)
	if location == "" {
		location = "global"
	}
	return fmt.Sprintf(
		"%s/projects/%s/locations/%s/publishers/google/models/%s:generateContent",
		baseURL,
		url.PathEscape(strings.TrimSpace(project)),
		url.PathEscape(location),
		url.PathEscape(strings.TrimSpace(model)),
	)
}

func ProbeVertexModel(
	ctx context.Context,
	client HTTPDoer,
	baseURL string,
	credentials VertexAuditCredentials,
	model string,
) AuditResult {
	if model == "auto" {
		code := http.StatusOK
		return AuditResult{Status: "ok", Reason: "runtime_default", HTTPStatus: &code}
	}
	if strings.TrimSpace(credentials.Token) == "" || strings.TrimSpace(credentials.Project) == "" {
		return AuditResult{Status: "unknown", Reason: "vertex_credentials_missing"}
	}
	payload := map[string]any{
		"contents": []map[string]any{
			{
				"role":  "user",
				"parts": []map[string]string{{"text": "Reply OK."}},
			},
		},
		"generationConfig": map[string]any{
			"maxOutputTokens": 16,
			"temperature":     0,
		},
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "invalid_payload"}
	}
	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		VertexModelURL(baseURL, credentials.Project, credentials.Location, model),
		bytes.NewReader(encoded),
	)
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "network_error"}
	}
	request.Header.Set("Authorization", "Bearer "+strings.TrimSpace(credentials.Token))
	request.Header.Set("Content-Type", "application/json")
	response, err := client.Do(request)
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "network_error"}
	}
	body := readResponseText(response, 700)
	status, reason := ClassifyProbe(response.StatusCode, true, body)
	code := response.StatusCode
	return AuditResult{Status: status, Reason: reason, HTTPStatus: &code}
}

func AuditVertexProvider(
	ctx context.Context,
	client HTTPDoer,
	baseURL string,
	credentials VertexAuditCredentials,
	state *CapabilityState,
	now int64,
) ProviderAuditReport {
	hasCredentials := strings.TrimSpace(credentials.Token) != "" && strings.TrimSpace(credentials.Project) != ""
	keyResult := AuditResult{Status: "missing", Reason: "vertex_credentials_missing"}
	if hasCredentials {
		code := http.StatusOK
		keyResult = AuditResult{Status: "ok", Reason: "service_account_valid", HTTPStatus: &code}
	}
	report := ProviderAuditReport{
		Key:    keyResult,
		Models: map[string]AuditResult{},
	}
	for _, choice := range CandidateModelChoices["vertex"] {
		result := ProbeVertexModel(ctx, client, baseURL, credentials, choice.Model)
		report.Models[choice.Model] = result
		if state != nil {
			state.SetModelRecord("vertex", choice.Model, result.Status, result.Reason, result.HTTPStatus, now)
		}
	}
	return report
}
