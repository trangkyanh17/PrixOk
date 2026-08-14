package runtimecfg

import (
	"math"
	"sort"
	"strconv"
	"strings"
	"sync"
)

type RouterWindow struct {
	Ratio         float64
	ObservedAt    float64
	WindowSeconds float64
}

type SmartRouterState struct {
	mu sync.Mutex

	LatencyMS      map[string]float64
	RequestRatio   map[string]float64
	TokenRatio     map[string]float64
	RequestResetAt map[string]float64
	TokenResetAt   map[string]float64
	CurrentWeight  map[string]float64
	Windows        map[string]map[string]RouterWindow
	Bottleneck     map[string]string
	CooldownUntil  map[string]float64
}

type RouterProviderStatus struct {
	EffectiveWeight       float64
	EWMAMS                *float64
	RequestRemainingRatio *float64
	TokenRemainingRatio   *float64
	Bottleneck            string
	Windows               map[string]RouterWindow
	RequestResetInSeconds *float64
	TokenResetInSeconds   *float64
	CooldownInSeconds     float64
}

type SmartRouterStatus struct {
	Enabled   bool
	Providers map[string]RouterProviderStatus
}

var routerPrimaryOrder = []string{"cerebras_gptoss", "groq_gptoss"}

var routerWindowOrder = []string{
	"req_minute",
	"req_hour",
	"req_day",
	"tok_minute",
	"tok_hour",
	"tok_day",
}

func NewSmartRouterState() *SmartRouterState {
	return &SmartRouterState{
		LatencyMS:      map[string]float64{},
		RequestRatio:   map[string]float64{},
		TokenRatio:     map[string]float64{},
		RequestResetAt: map[string]float64{},
		TokenResetAt:   map[string]float64{},
		CurrentWeight: map[string]float64{
			"cerebras_gptoss": 0,
			"groq_gptoss":     0,
		},
		Windows:       map[string]map[string]RouterWindow{},
		Bottleneck:    map[string]string{},
		CooldownUntil: map[string]float64{},
	}
}

// ensureLocked initializes maps. Callers must hold state.mu, except construction.
func (state *SmartRouterState) ensureLocked() {
	if state.LatencyMS == nil {
		state.LatencyMS = map[string]float64{}
	}
	if state.RequestRatio == nil {
		state.RequestRatio = map[string]float64{}
	}
	if state.TokenRatio == nil {
		state.TokenRatio = map[string]float64{}
	}
	if state.RequestResetAt == nil {
		state.RequestResetAt = map[string]float64{}
	}
	if state.TokenResetAt == nil {
		state.TokenResetAt = map[string]float64{}
	}
	if state.CurrentWeight == nil {
		state.CurrentWeight = map[string]float64{}
	}
	if state.Windows == nil {
		state.Windows = map[string]map[string]RouterWindow{}
	}
	if state.Bottleneck == nil {
		state.Bottleneck = map[string]string{}
	}
	if state.CooldownUntil == nil {
		state.CooldownUntil = map[string]float64{}
	}
}

func routerHeader(headers map[string]string, key string) string {
	for name, value := range headers {
		if strings.EqualFold(strings.TrimSpace(name), key) {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func routerSafeFloat(value string) (float64, bool) {
	parsed, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
	if err != nil || math.IsNaN(parsed) || math.IsInf(parsed, 0) {
		return 0, false
	}
	return parsed, true
}

func RouterRatio(remaining, limit string) (float64, bool) {
	r, ok := routerSafeFloat(remaining)
	if !ok {
		return 0, false
	}
	l, ok := routerSafeFloat(limit)
	if !ok || l <= 0 {
		return 0, false
	}
	return clampFloat(r/l, 0, 1), true
}

func RouterResetSeconds(value string) (float64, bool) {
	text := strings.ToLower(strings.TrimSpace(value))
	if text == "" {
		return 0, false
	}
	if direct, ok := routerSafeFloat(text); ok {
		return math.Max(0, direct), true
	}

	total := 0.0
	number := strings.Builder{}
	parsedAny := false
	flush := func(unit byte) bool {
		if number.Len() == 0 {
			return false
		}
		amount, err := strconv.ParseFloat(number.String(), 64)
		number.Reset()
		if err != nil {
			return false
		}
		switch unit {
		case 'h':
			total += amount * 3600
		case 'm':
			total += amount * 60
		case 's':
			total += amount
		default:
			return false
		}
		parsedAny = true
		return true
	}

	for i := 0; i < len(text); i++ {
		ch := text[i]
		if (ch >= '0' && ch <= '9') || ch == '.' {
			number.WriteByte(ch)
			continue
		}
		if ch == 'h' || ch == 'm' || ch == 's' {
			if !flush(ch) {
				return 0, false
			}
			continue
		}
		if ch == ' ' || ch == '\t' {
			continue
		}
		return 0, false
	}
	if number.Len() > 0 {
		amount, err := strconv.ParseFloat(number.String(), 64)
		if err != nil {
			return 0, false
		}
		total += amount
		parsedAny = true
	}
	if !parsedAny {
		return 0, false
	}
	return math.Max(0, total), true
}

func clampFloat(value, minimum, maximum float64) float64 {
	if value < minimum {
		return minimum
	}
	if value > maximum {
		return maximum
	}
	return value
}

func routerFloat(values map[string]string, key string, fallback, minimum, maximum float64) float64 {
	value, ok := routerSafeFloat(values[key])
	if !ok {
		value = fallback
	}
	return clampFloat(value, minimum, maximum)
}

func routerTruthy(value string, fallback bool) bool {
	value = strings.ToLower(strings.TrimSpace(value))
	if value == "" {
		return fallback
	}
	switch value {
	case "1", "true", "yes", "on", "enable", "enabled":
		return true
	default:
		return false
	}
}

func minRatioKey(values map[string]float64, order []string) string {
	selected := ""
	minimum := math.Inf(1)
	seen := map[string]bool{}
	for _, key := range order {
		value, ok := values[key]
		if !ok {
			continue
		}
		seen[key] = true
		if value < minimum {
			minimum = value
			selected = key
		}
	}
	if len(seen) == len(values) {
		return selected
	}
	keys := make([]string, 0, len(values))
	for key := range values {
		if !seen[key] {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	for _, key := range keys {
		value := values[key]
		if value < minimum {
			minimum = value
			selected = key
		}
	}
	return selected
}

func (state *SmartRouterState) storeWindowLocked(name, window string, ratio, windowSeconds, now float64) {
	state.ensureLocked()
	if state.Windows[name] == nil {
		state.Windows[name] = map[string]RouterWindow{}
	}
	state.Windows[name][window] = RouterWindow{
		Ratio:         clampFloat(ratio, 0, 1),
		ObservedAt:    now,
		WindowSeconds: math.Max(1, windowSeconds),
	}
}

func (state *SmartRouterState) currentWindowRatiosLocked(name string, now float64) map[string]float64 {
	state.ensureLocked()
	windows := state.Windows[name]
	if len(windows) == 0 {
		return map[string]float64{}
	}
	current := make(map[string]float64, len(windows))
	for window, item := range windows {
		estimated := clampFloat(
			item.Ratio+math.Max(0, now-item.ObservedAt)/math.Max(1, item.WindowSeconds),
			0,
			1,
		)
		current[window] = estimated
		if estimated >= 0.999999 {
			delete(windows, window)
		}
	}
	if len(windows) == 0 {
		delete(state.Windows, name)
	}
	return current
}

func (state *SmartRouterState) CurrentWindowRatios(name string, now float64) map[string]float64 {
	if state == nil {
		return map[string]float64{}
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	return state.currentWindowRatiosLocked(name, now)
}

func (state *SmartRouterState) CaptureRateHeaders(provider string, headers map[string]string, now float64) {
	if state == nil {
		return
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	state.ensureLocked()
	provider = strings.ToLower(strings.TrimSpace(provider))

	if provider == "cerebras" {
		name := "cerebras_gptoss"
		windows := []struct {
			Name      string
			Remaining string
			Limit     string
			Seconds   float64
		}{
			{"req_minute", "x-ratelimit-remaining-requests-minute", "x-ratelimit-limit-requests-minute", 60},
			{"req_hour", "x-ratelimit-remaining-requests-hour", "x-ratelimit-limit-requests-hour", 3600},
			{"req_day", "x-ratelimit-remaining-requests-day", "x-ratelimit-limit-requests-day", 86400},
			{"tok_minute", "x-ratelimit-remaining-tokens-minute", "x-ratelimit-limit-tokens-minute", 60},
			{"tok_hour", "x-ratelimit-remaining-tokens-hour", "x-ratelimit-limit-tokens-hour", 3600},
			{"tok_day", "x-ratelimit-remaining-tokens-day", "x-ratelimit-limit-tokens-day", 86400},
		}
		for _, window := range windows {
			ratio, ok := RouterRatio(routerHeader(headers, window.Remaining), routerHeader(headers, window.Limit))
			if ok {
				state.storeWindowLocked(name, window.Name, ratio, window.Seconds, now)
			}
		}
		current := state.currentWindowRatiosLocked(name, now)
		if len(current) > 0 {
			state.Bottleneck[name] = minRatioKey(current, routerWindowOrder)
		}
		return
	}
	if provider != "groq" {
		return
	}

	name := "groq_gptoss"
	if ratio, ok := RouterRatio(routerHeader(headers, "x-ratelimit-remaining-requests"), routerHeader(headers, "x-ratelimit-limit-requests")); ok {
		state.RequestRatio[name] = ratio
	}
	if ratio, ok := RouterRatio(routerHeader(headers, "x-ratelimit-remaining-tokens"), routerHeader(headers, "x-ratelimit-limit-tokens")); ok {
		state.TokenRatio[name] = ratio
	}
	if seconds, ok := RouterResetSeconds(routerHeader(headers, "x-ratelimit-reset-requests")); ok {
		state.RequestResetAt[name] = now + seconds
	}
	if seconds, ok := RouterResetSeconds(routerHeader(headers, "x-ratelimit-reset-tokens")); ok {
		state.TokenResetAt[name] = now + seconds
	}
	candidates := map[string]float64{}
	if ratio, ok := state.RequestRatio[name]; ok {
		candidates["req_day"] = ratio
	}
	if ratio, ok := state.TokenRatio[name]; ok {
		candidates["tok_minute"] = ratio
	}
	if len(candidates) > 0 {
		state.Bottleneck[name] = minRatioKey(candidates, []string{"req_day", "tok_minute"})
	}
}

func RouterBaseWeight(name string, values map[string]string) float64 {
	switch name {
	case "cerebras_gptoss":
		return routerFloat(values, "ATRI_FREE_WEIGHT_CEREBRAS", 4, 0.1, 20)
	case "groq_gptoss":
		return routerFloat(values, "ATRI_FREE_WEIGHT_GROQ", 1, 0.1, 20)
	default:
		return 0
	}
}

func (state *SmartRouterState) effectiveWeightLocked(name string, values map[string]string, now float64) float64 {
	state.ensureLocked()
	if state.CooldownUntil[name] > now {
		return 0
	}
	weight := RouterBaseWeight(name, values)
	if name == "cerebras_gptoss" {
		current := state.currentWindowRatiosLocked(name, now)
		if len(current) > 0 {
			bottleneck := minRatioKey(current, routerWindowOrder)
			state.Bottleneck[name] = bottleneck
			weight *= math.Max(0.05, current[bottleneck])
			requestValues := make([]float64, 0, len(current))
			tokenValues := make([]float64, 0, len(current))
			for window, ratio := range current {
				if strings.HasPrefix(window, "req_") {
					requestValues = append(requestValues, ratio)
				}
				if strings.HasPrefix(window, "tok_") {
					tokenValues = append(tokenValues, ratio)
				}
			}
			if len(requestValues) > 0 {
				state.RequestRatio[name] = minimumFloat(requestValues)
			} else {
				delete(state.RequestRatio, name)
			}
			if len(tokenValues) > 0 {
				state.TokenRatio[name] = minimumFloat(tokenValues)
			} else {
				delete(state.TokenRatio, name)
			}
		} else {
			delete(state.RequestRatio, name)
			delete(state.TokenRatio, name)
			delete(state.Bottleneck, name)
		}
	} else {
		requestRatio, hasRequest := state.RequestRatio[name]
		if hasRequest {
			if reset, ok := state.RequestResetAt[name]; ok && reset <= now {
				hasRequest = false
				delete(state.RequestRatio, name)
				delete(state.RequestResetAt, name)
			}
		}
		tokenRatio, hasToken := state.TokenRatio[name]
		if hasToken {
			if reset, ok := state.TokenResetAt[name]; ok && reset <= now {
				hasToken = false
				delete(state.TokenRatio, name)
				delete(state.TokenResetAt, name)
			}
		}
		ratios := []float64{}
		if hasRequest {
			ratios = append(ratios, requestRatio)
		}
		if hasToken {
			ratios = append(ratios, tokenRatio)
		}
		if len(ratios) > 0 {
			weight *= math.Max(0.05, minimumFloat(ratios))
		}
	}
	if latency, ok := state.LatencyMS[name]; ok {
		latencyFactor := 1000 / math.Max(500, latency)
		weight *= clampFloat(latencyFactor, 0.65, 1.35)
	}
	return math.Max(0, weight)
}

func (state *SmartRouterState) EffectiveWeight(name string, values map[string]string, now float64) float64 {
	if state == nil {
		return 0
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	return state.effectiveWeightLocked(name, values, now)
}

func minimumFloat(values []float64) float64 {
	minimum := math.Inf(1)
	for _, value := range values {
		if value < minimum {
			minimum = value
		}
	}
	if math.IsInf(minimum, 1) {
		return 0
	}
	return minimum
}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func (state *SmartRouterState) SmartOrder(chain []string, values map[string]string, now float64) []string {
	if state == nil {
		return append([]string(nil), chain...)
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	state.ensureLocked()
	if !routerTruthy(values["ATRI_FREE_SMART_ROUTER"], true) {
		return append([]string(nil), chain...)
	}
	primary := make([]string, 0, len(routerPrimaryOrder))
	for _, name := range routerPrimaryOrder {
		spec, known := FreeProviderDefinitions[name]
		if !known || !containsString(chain, name) {
			continue
		}
		if strings.TrimSpace(values[spec.KeyName]) == "" || state.CooldownUntil[name] > now {
			continue
		}
		primary = append(primary, name)
	}
	if len(primary) < 2 {
		return append([]string(nil), chain...)
	}
	weights := make(map[string]float64, len(primary))
	total := 0.0
	for _, name := range primary {
		weight := state.effectiveWeightLocked(name, values, now)
		weights[name] = weight
		total += weight
	}
	if total <= 0 {
		return append([]string(nil), chain...)
	}
	for _, name := range primary {
		state.CurrentWeight[name] += weights[name]
	}
	selected := primary[0]
	for _, name := range primary[1:] {
		if state.CurrentWeight[name] > state.CurrentWeight[selected] {
			selected = name
		}
	}
	state.CurrentWeight[selected] -= total

	restPrimary := make([]string, 0, len(primary)-1)
	for _, name := range primary {
		if name != selected {
			restPrimary = append(restPrimary, name)
		}
	}
	sort.SliceStable(restPrimary, func(i, j int) bool { return weights[restPrimary[i]] > weights[restPrimary[j]] })
	fallback := make([]string, 0, len(chain))
	for _, name := range chain {
		if !containsString(primary, name) {
			fallback = append(fallback, name)
		}
	}
	ordered := []string{selected}
	ordered = append(ordered, restPrimary...)
	ordered = append(ordered, fallback...)
	return ordered
}

func (state *SmartRouterState) RecordLatency(name string, elapsedMS float64, values map[string]string) {
	if state == nil {
		return
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	state.ensureLocked()
	if name != "cerebras_gptoss" && name != "groq_gptoss" {
		return
	}
	alpha := routerFloat(values, "ATRI_FREE_LATENCY_EWMA_ALPHA", 0.25, 0.05, 1)
	if old, ok := state.LatencyMS[name]; ok {
		state.LatencyMS[name] = alpha*elapsedMS + (1-alpha)*old
	} else {
		state.LatencyMS[name] = elapsedMS
	}
}

func (state *SmartRouterState) CooldownFor(name string, spec FreeProviderSpec) float64 {
	if state == nil {
		return 0
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	state.ensureLocked()
	return FreePoolCooldownUntil(name, spec, state.CooldownUntil)
}

func (state *SmartRouterState) SetCooldown(key string, until float64) {
	if state == nil || strings.TrimSpace(key) == "" {
		return
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	state.ensureLocked()
	state.CooldownUntil[key] = until
}

func pointerFloat(value float64) *float64 {
	copyValue := value
	return &copyValue
}

func (state *SmartRouterState) Status(values map[string]string, now float64) SmartRouterStatus {
	if state == nil {
		return SmartRouterStatus{Enabled: routerTruthy(values["ATRI_FREE_SMART_ROUTER"], true), Providers: map[string]RouterProviderStatus{}}
	}
	state.mu.Lock()
	defer state.mu.Unlock()
	state.ensureLocked()
	providers := map[string]RouterProviderStatus{}
	for _, name := range routerPrimaryOrder {
		provider := RouterProviderStatus{
			EffectiveWeight:   state.effectiveWeightLocked(name, values, now),
			Bottleneck:        state.Bottleneck[name],
			Windows:           map[string]RouterWindow{},
			CooldownInSeconds: math.Max(0, state.CooldownUntil[name]-now),
		}
		if value, ok := state.LatencyMS[name]; ok {
			provider.EWMAMS = pointerFloat(value)
		}
		if value, ok := state.RequestRatio[name]; ok {
			provider.RequestRemainingRatio = pointerFloat(value)
		}
		if value, ok := state.TokenRatio[name]; ok {
			provider.TokenRemainingRatio = pointerFloat(value)
		}
		if reset, ok := state.RequestResetAt[name]; ok {
			provider.RequestResetInSeconds = pointerFloat(math.Max(0, reset-now))
		}
		if reset, ok := state.TokenResetAt[name]; ok {
			provider.TokenResetInSeconds = pointerFloat(math.Max(0, reset-now))
		}
		if name == "cerebras_gptoss" {
			current := state.currentWindowRatiosLocked(name, now)
			for window, ratio := range current {
				item, ok := state.Windows[name][window]
				if !ok {
					continue
				}
				provider.Windows[window] = RouterWindow{Ratio: ratio, ObservedAt: item.ObservedAt, WindowSeconds: item.WindowSeconds}
			}
		}
		providers[name] = provider
	}
	return SmartRouterStatus{
		Enabled:   routerTruthy(values["ATRI_FREE_SMART_ROUTER"], true),
		Providers: providers,
	}
}
