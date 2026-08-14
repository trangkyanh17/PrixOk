package runtimecfg

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sort"
	"strings"
)

type HTTPDoer interface {
	Do(*http.Request) (*http.Response, error)
}

type OpenAIProviderEndpoints struct {
	Key    string
	Models string
	Chat   string
}

var DefaultOpenAIProviderEndpoints = map[string]OpenAIProviderEndpoints{
	"cerebras": {
		Key:    "https://api.cerebras.ai/v1/models",
		Models: "https://api.cerebras.ai/v1/models",
		Chat:   "https://api.cerebras.ai/v1/chat/completions",
	},
	"groq": {
		Key:    "https://api.groq.com/openai/v1/models",
		Models: "https://api.groq.com/openai/v1/models",
		Chat:   "https://api.groq.com/openai/v1/chat/completions",
	},
	"openrouter": {
		Key:    "https://openrouter.ai/api/v1/key",
		Models: "https://openrouter.ai/api/v1/models",
		Chat:   "https://openrouter.ai/api/v1/chat/completions",
	},
}

func readResponseText(response *http.Response, limit int64) string {
	if response == nil || response.Body == nil {
		return ""
	}
	defer response.Body.Close()
	reader := io.Reader(response.Body)
	if limit > 0 {
		reader = io.LimitReader(response.Body, limit)
	}
	payload, _ := io.ReadAll(reader)
	return string(payload)
}

func authorizedRequest(ctx context.Context, method, url, key string, body io.Reader) (*http.Request, error) {
	request, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		return nil, err
	}
	request.Header.Set("Authorization", "Bearer "+key)
	return request, nil
}

func CheckProviderKey(ctx context.Context, client HTTPDoer, url, key string) AuditResult {
	if strings.TrimSpace(key) == "" {
		return AuditResult{Status: "missing", Reason: "key_missing"}
	}
	request, err := authorizedRequest(ctx, http.MethodGet, url, key, nil)
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "network_error"}
	}
	response, err := client.Do(request)
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "network_error"}
	}
	_ = readResponseText(response, 1024)
	status, reason := ClassifyKeyCheck(response.StatusCode, true)
	code := response.StatusCode
	return AuditResult{Status: status, Reason: reason, HTTPStatus: &code}
}

func DiscoverOpenAIModels(
	ctx context.Context,
	client HTTPDoer,
	provider, url, key string,
	state *CapabilityState,
) (AuditResult, []string) {
	request, err := authorizedRequest(ctx, http.MethodGet, url, key, nil)
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "network_error"}, nil
	}
	response, err := client.Do(request)
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "network_error"}, nil
	}
	body := readResponseText(response, 4<<20)
	code := response.StatusCode
	if code < 200 || code >= 300 {
		return AuditResult{Status: "unknown", Reason: fmt.Sprintf("http_%d", code), HTTPStatus: &code}, nil
	}

	var payload struct {
		Data []map[string]any `json:"data"`
	}
	if err := json.Unmarshal([]byte(body), &payload); err != nil {
		return AuditResult{Status: "unknown", Reason: "JSONDecodeError", HTTPStatus: &code}, nil
	}

	seen := map[string]bool{}
	models := make([]string, 0, len(payload.Data))
	for _, item := range payload.Data {
		id, ok := item["id"].(string)
		if !ok || strings.TrimSpace(id) == "" || seen[id] {
			continue
		}
		seen[id] = true
		models = append(models, id)
	}
	sort.Strings(models)
	if state != nil {
		state.normalize()
		state.Discovered[strings.ToLower(strings.TrimSpace(provider))] = append([]string(nil), models...)
	}
	return AuditResult{Status: "ok", Reason: "models_discovered", HTTPStatus: &code}, models
}

func ProbeOpenAIModel(
	ctx context.Context,
	client HTTPDoer,
	provider, url, key, model string,
) AuditResult {
	if strings.TrimSpace(key) == "" {
		return AuditResult{Status: "unknown", Reason: "key_missing"}
	}
	payload := BuildChatPayload(
		provider,
		model,
		[]map[string]string{{"role": "user", "content": "Reply OK."}},
		"medium",
		16,
		0,
	)
	encoded, err := json.Marshal(payload)
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "invalid_payload"}
	}
	request, err := authorizedRequest(ctx, http.MethodPost, url, key, bytes.NewReader(encoded))
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "network_error"}
	}
	for name, value := range BuildProviderHeaders(provider, key) {
		request.Header.Set(name, value)
	}
	response, err := client.Do(request)
	if err != nil {
		return AuditResult{Status: "unknown", Reason: "network_error"}
	}
	body := readResponseText(response, 700)
	status, reason := ClassifyProbe(response.StatusCode, true, body)
	code := response.StatusCode
	return AuditResult{Status: status, Reason: reason, HTTPStatus: &code}
}

func AuditOpenAIProvider(
	ctx context.Context,
	client HTTPDoer,
	provider, key string,
	endpoints OpenAIProviderEndpoints,
	state *CapabilityState,
	now int64,
) ProviderAuditReport {
	provider = strings.ToLower(strings.TrimSpace(provider))
	keyResult := CheckProviderKey(ctx, client, endpoints.Key, key)
	report := ProviderAuditReport{
		Key:    keyResult,
		Models: map[string]AuditResult{},
	}
	choices := CandidateModelChoices[provider]
	if keyResult.Status != "ok" {
		for _, choice := range choices {
			result := AuditResult{
				Status:     "unknown",
				Reason:     keyResult.Reason,
				HTTPStatus: keyResult.HTTPStatus,
			}
			report.Models[choice.Model] = result
			if state != nil {
				state.SetModelRecord(provider, choice.Model, result.Status, result.Reason, result.HTTPStatus, now)
			}
		}
		return report
	}

	discovery, discoveredModels := DiscoverOpenAIModels(ctx, client, provider, endpoints.Models, key, state)
	discovered := make(map[string]bool, len(discoveredModels))
	for _, model := range discoveredModels {
		discovered[model] = true
	}
	for _, choice := range choices {
		var result AuditResult
		if discovery.Status == "ok" && !discovered[choice.Model] {
			code := http.StatusNotFound
			result = AuditResult{Status: "dead", Reason: "model_not_listed", HTTPStatus: &code}
		} else {
			result = ProbeOpenAIModel(ctx, client, provider, endpoints.Chat, key, choice.Model)
		}
		report.Models[choice.Model] = result
		if state != nil {
			state.SetModelRecord(provider, choice.Model, result.Status, result.Reason, result.HTTPStatus, now)
		}
	}
	return report
}

func AuditOpenAIProviders(
	ctx context.Context,
	client HTTPDoer,
	providers []string,
	keys map[string]string,
	endpoints map[string]OpenAIProviderEndpoints,
	state *CapabilityState,
	now int64,
) map[string]ProviderAuditReport {
	if len(providers) == 0 {
		providers = []string{"cerebras", "groq", "openrouter"}
	}
	report := map[string]ProviderAuditReport{}
	for _, provider := range providers {
		provider = strings.ToLower(strings.TrimSpace(provider))
		providerEndpoints, ok := endpoints[provider]
		if !ok {
			continue
		}
		report[provider] = AuditOpenAIProvider(
			ctx,
			client,
			provider,
			strings.TrimSpace(keys[provider]),
			providerEndpoints,
			state,
			now,
		)
	}
	if state != nil {
		state.LastAuditAt = now
	}
	return report
}
