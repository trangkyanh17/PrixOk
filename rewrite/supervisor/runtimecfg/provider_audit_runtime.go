package runtimecfg

import (
	"context"
	"strings"
	"time"
)

type ProviderAuditOptions struct {
	Providers         []string
	Keys              map[string]string
	OpenAIEndpoints   map[string]OpenAIProviderEndpoints
	VertexBaseURL     string
	VertexCredentials VertexAuditCredentials
	StatePath         string
	Now               int64
}

func requestedAuditProviders(providers []string) []string {
	if len(providers) == 0 {
		return []string{"cerebras", "groq", "openrouter", "vertex"}
	}
	seen := map[string]bool{}
	result := make([]string, 0, len(providers))
	for _, provider := range providers {
		provider = strings.ToLower(strings.TrimSpace(provider))
		if provider == "" || seen[provider] {
			continue
		}
		seen[provider] = true
		result = append(result, provider)
	}
	return result
}

func AuditCapabilities(
	ctx context.Context,
	client HTTPDoer,
	options ProviderAuditOptions,
	state *CapabilityState,
) (map[string]ProviderAuditReport, error) {
	if state == nil {
		blank := BlankCapabilityState()
		state = &blank
	}
	state.normalize()
	now := options.Now
	if now <= 0 {
		now = time.Now().Unix()
	}
	endpoints := options.OpenAIEndpoints
	if endpoints == nil {
		endpoints = DefaultOpenAIProviderEndpoints
	}
	keys := options.Keys
	if keys == nil {
		keys = map[string]string{}
	}

	report := map[string]ProviderAuditReport{}
	for _, provider := range requestedAuditProviders(options.Providers) {
		if provider == "vertex" {
			report[provider] = AuditVertexProvider(
				ctx,
				client,
				options.VertexBaseURL,
				options.VertexCredentials,
				state,
				now,
			)
			continue
		}
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

	state.LastAuditAt = now
	if strings.TrimSpace(options.StatePath) != "" {
		if err := SaveCapabilityState(options.StatePath, state, now); err != nil {
			return report, err
		}
	}
	return report, nil
}
