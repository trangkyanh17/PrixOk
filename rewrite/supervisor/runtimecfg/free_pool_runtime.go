package runtimecfg

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

var freePoolMonotonicOrigin = time.Now()

type FreeReply struct {
	Text     string
	Provider string
	Model    string
}

type FreeProviderCallError struct {
	Message    string
	StatusCode int
	HasStatus  bool
}

func (err *FreeProviderCallError) Error() string {
	return err.Message
}

type FreeChatRequest struct {
	SystemInstruction string
	History           []map[string]any
	CurrentParts      []map[string]any
	ThinkingLevel     string
	TaskType          string
}

type FreePoolRuntime struct {
	Client              HTTPDoer
	Values              map[string]string
	Control             ControlState
	Capabilities        *CapabilityState
	CapabilityStatePath string
	Router              *SmartRouterState
}

func httpHeaderValues(headers http.Header) map[string]string {
	values := make(map[string]string, len(headers))
	for name, items := range headers {
		if len(items) > 0 {
			values[name] = items[0]
		}
	}
	return values
}

func CallFreeProvider(
	ctx context.Context,
	client HTTPDoer,
	spec FreeProviderSpec,
	apiKey string,
	messages []map[string]string,
	thinkingLevel string,
	maxTokens int,
	timeoutSeconds int,
	router *SmartRouterState,
) (string, error) {
	payload := BuildChatPayload(
		spec.Provider,
		spec.Model,
		messages,
		thinkingLevel,
		maxTokens,
		0.7,
	)
	encoded, err := json.Marshal(payload)
	if err != nil {
		return "", &FreeProviderCallError{Message: "invalid_payload"}
	}

	requestContext := ctx
	cancel := func() {}
	if timeoutSeconds > 0 {
		requestContext, cancel = context.WithTimeout(ctx, time.Duration(timeoutSeconds)*time.Second)
	}
	defer cancel()

	request, err := http.NewRequestWithContext(
		requestContext,
		http.MethodPost,
		spec.URL,
		bytes.NewReader(encoded),
	)
	if err != nil {
		return "", &FreeProviderCallError{Message: "network:" + err.Error()}
	}
	for name, value := range BuildProviderHeaders(spec.Provider, apiKey) {
		request.Header.Set(name, value)
	}

	response, err := client.Do(request)
	if err != nil {
		return "", &FreeProviderCallError{Message: "network:" + err.Error()}
	}
	body := readResponseText(response, 4<<20)
	if router != nil {
		router.CaptureRateHeaders(spec.Provider, httpHeaderValues(response.Header), monotonicSeconds())
	}
	if response.StatusCode >= 400 {
		body = strings.ReplaceAll(body, "\n", " ")
		body = truncateRunes(body, 240)
		return "", &FreeProviderCallError{
			Message:    fmt.Sprintf("http:%d:%s", response.StatusCode, body),
			StatusCode: response.StatusCode,
			HasStatus:  true,
		}
	}

	var responsePayload map[string]any
	if err := json.Unmarshal([]byte(body), &responsePayload); err != nil {
		return "", &FreeProviderCallError{Message: "invalid_json"}
	}
	text := ExtractFreePoolResponseText(responsePayload)
	if text == "" {
		return "", &FreeProviderCallError{Message: "empty_text"}
	}
	return text, nil
}

func monotonicSeconds() float64 {
	return time.Since(freePoolMonotonicOrigin).Seconds()
}

func (runtime *FreePoolRuntime) runtimeClient() HTTPDoer {
	if runtime.Client != nil {
		return runtime.Client
	}
	return http.DefaultClient
}

func (runtime *FreePoolRuntime) runtimeValues() map[string]string {
	if runtime.Values == nil {
		return map[string]string{}
	}
	return runtime.Values
}

func (runtime *FreePoolRuntime) runtimeRouter() *SmartRouterState {
	if runtime.Router == nil {
		runtime.Router = NewSmartRouterState()
	}
	return runtime.Router
}

func manualFreePoolName(providerMode string) string {
	switch providerMode {
	case "cerebras":
		return "cerebras_gptoss"
	case "groq":
		return "groq_gptoss"
	case "openrouter":
		return "openrouter_free"
	default:
		return ""
	}
}

func (runtime *FreePoolRuntime) healControl() ControlState {
	state := NormalizeControlState(runtime.Control)
	if runtime.Capabilities != nil {
		state, _ = HealControlState(state, *runtime.Capabilities)
	}
	runtime.Control = state
	return state
}

func cloneFreeProviderSpec(spec FreeProviderSpec) FreeProviderSpec {
	return FreeProviderSpec{
		Provider: spec.Provider,
		KeyName:  spec.KeyName,
		URL:      spec.URL,
		Model:    spec.Model,
	}
}

func (runtime *FreePoolRuntime) markTerminalModelFailure(spec FreeProviderSpec, callErr *FreeProviderCallError) {
	if runtime.Capabilities == nil || !IsTerminalModelError(callErr.StatusCode, callErr.HasStatus, callErr.Error()) {
		return
	}
	runtime.Capabilities.MarkModelUnavailable(
		spec.Provider,
		spec.Model,
		callErr.Error(),
		time.Now().Unix(),
	)
	if strings.TrimSpace(runtime.CapabilityStatePath) != "" {
		_ = SaveCapabilityState(runtime.CapabilityStatePath, runtime.Capabilities, time.Now().Unix())
	}
}

func (runtime *FreePoolRuntime) GenerateFreeChat(
	ctx context.Context,
	request FreeChatRequest,
) (*FreeReply, error) {
	values := runtime.runtimeValues()
	if !routerTruthy(values["ATRI_FREE_POOL_ENABLED"], true) {
		return nil, nil
	}
	messages := BuildFreePoolMessages(
		request.SystemInstruction,
		request.History,
		request.CurrentParts,
	)
	if messages == nil {
		return nil, nil
	}

	taskType := NormalizeFreePoolTask(request.TaskType)
	control := runtime.healControl()
	providerMode := ResolveProviderMode(control)
	if providerMode == "vertex" {
		return nil, nil
	}

	var chain []string
	if manualName := manualFreePoolName(providerMode); manualName != "" {
		chain = []string{manualName}
	} else {
		chain = FreePoolTaskChain(taskType)
		if taskType != "coding_agentic" {
			chain = runtime.runtimeRouter().SmartOrder(chain, values, monotonicSeconds())
		}
	}

	maxAttempts := FreePoolMaxAttempts(values, providerMode, taskType)
	thinkingLevel := strings.ToLower(strings.TrimSpace(request.ThinkingLevel))
	if thinkingLevel == "" {
		thinkingLevel = "medium"
	}
	maxTokens := FreePoolDynamicMaxTokens(values, thinkingLevel)
	timeoutSeconds := FreePoolRequestTimeoutSeconds(values)

	attempted := 0
	for _, name := range chain {
		if attempted >= maxAttempts {
			break
		}
		baseSpec, ok := FreeProviderDefinitions[name]
		if !ok {
			continue
		}
		spec := cloneFreeProviderSpec(baseSpec)
		if providerMode == "smart" {
			if fixedModel := FreePoolTaskFixedModel(taskType, name); fixedModel != "" {
				spec.Model = fixedModel
			}
		} else {
			spec.Model = ResolveProviderModel(control, spec.Provider, spec.Model)
		}

		apiKey := strings.TrimSpace(values[spec.KeyName])
		if apiKey == "" {
			continue
		}
		now := monotonicSeconds()
		if FreePoolCooldownUntil(name, spec, runtime.runtimeRouter().CooldownUntil) > now {
			continue
		}
		providerThinking := ResolveProviderThinking(control, spec.Provider, thinkingLevel)
		attempted++
		started := time.Now()

		text, err := CallFreeProvider(
			ctx,
			runtime.runtimeClient(),
			spec,
			apiKey,
			messages,
			providerThinking,
			maxTokens,
			timeoutSeconds,
			runtime.runtimeRouter(),
		)
		if err != nil {
			callErr, ok := err.(*FreeProviderCallError)
			if ok {
				runtime.markTerminalModelFailure(spec, callErr)
				cooldownKey := FreePoolFailureCooldownKey(name, spec, callErr.StatusCode)
				runtime.runtimeRouter().CooldownUntil[cooldownKey] = monotonicSeconds() + FreePoolFailureCooldownSeconds(callErr.StatusCode, callErr.HasStatus)
			} else {
				runtime.runtimeRouter().CooldownUntil[name] = monotonicSeconds() + FreePoolUnexpectedFailureCooldownSeconds()
			}
			continue
		}

		elapsedMS := float64(time.Since(started).Milliseconds())
		runtime.runtimeRouter().RecordLatency(name, elapsedMS, values)
		return &FreeReply{
			Text:     text,
			Provider: spec.Provider,
			Model:    spec.Model,
		}, nil
	}
	return nil, nil
}
