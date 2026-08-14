package runtimecfg

import (
	"math"
	"reflect"
	"testing"
)

func closeEnough(got, want float64) bool {
	return math.Abs(got-want) < 1e-9
}

func TestRouterResetSeconds(t *testing.T) {
	cases := map[string]float64{"90": 90, "1h2m3s": 3723, "1m30.5s": 90.5}
	for input, want := range cases {
		got, ok := RouterResetSeconds(input)
		if !ok || !closeEnough(got, want) {
			t.Fatalf("input=%q got=%v ok=%v want=%v", input, got, ok, want)
		}
	}
	if _, ok := RouterResetSeconds("garbage"); ok {
		t.Fatal("garbage reset unexpectedly parsed")
	}
}

func TestCerebrasRateWindowsRecoverLinearly(t *testing.T) {
	state := NewSmartRouterState()
	state.CaptureRateHeaders("cerebras", map[string]string{
		"X-RateLimit-Remaining-Requests-Minute": "20",
		"X-RateLimit-Limit-Requests-Minute": "100",
		"X-RateLimit-Remaining-Tokens-Hour": "50",
		"X-RateLimit-Limit-Tokens-Hour": "100",
	}, 100)
	if got := state.Bottleneck["cerebras_gptoss"]; got != "req_minute" {
		t.Fatalf("bottleneck=%q", got)
	}
	if got := state.EffectiveWeight("cerebras_gptoss", map[string]string{}, 100); !closeEnough(got, 0.8) {
		t.Fatalf("weight=%v want=0.8", got)
	}
	current := state.CurrentWindowRatios("cerebras_gptoss", 130)
	if got := current["req_minute"]; !closeEnough(got, 0.7) {
		t.Fatalf("req_minute=%v want=0.7", got)
	}
}

func TestGroqRatiosExpireAtReset(t *testing.T) {
	state := NewSmartRouterState()
	state.CaptureRateHeaders("groq", map[string]string{
		"x-ratelimit-remaining-requests": "50",
		"x-ratelimit-limit-requests": "100",
		"x-ratelimit-remaining-tokens": "20",
		"x-ratelimit-limit-tokens": "100",
		"x-ratelimit-reset-requests": "120s",
		"x-ratelimit-reset-tokens": "10s",
	}, 100)
	if got := state.EffectiveWeight("groq_gptoss", map[string]string{}, 100); !closeEnough(got, 0.2) {
		t.Fatalf("initial weight=%v want=0.2", got)
	}
	if got := state.EffectiveWeight("groq_gptoss", map[string]string{}, 111); !closeEnough(got, 0.5) {
		t.Fatalf("post-token-reset weight=%v want=0.5", got)
	}
	if _, ok := state.TokenRatio["groq_gptoss"]; ok {
		t.Fatal("token ratio should be removed after reset")
	}
}

func TestSmartOrderMatchesWeightedPrimaryPolicy(t *testing.T) {
	state := NewSmartRouterState()
	values := map[string]string{"CEREBRAS_API_KEY": "c-key", "GROQ_API_KEY": "g-key"}
	chain := []string{"groq_gptoss", "cerebras_gptoss", "openrouter_free"}
	first := state.SmartOrder(chain, values, 1)
	second := state.SmartOrder(chain, values, 2)
	third := state.SmartOrder(chain, values, 3)
	if first[0] != "cerebras_gptoss" || second[0] != "cerebras_gptoss" || third[0] != "groq_gptoss" {
		t.Fatalf("unexpected weighted sequence: %v %v %v", first, second, third)
	}
	if !reflect.DeepEqual(first[2:], []string{"openrouter_free"}) {
		t.Fatalf("fallback order changed: %v", first)
	}
}

func TestSmartOrderCanBeDisabled(t *testing.T) {
	state := NewSmartRouterState()
	chain := []string{"groq_gptoss", "cerebras_gptoss"}
	got := state.SmartOrder(chain, map[string]string{
		"ATRI_FREE_SMART_ROUTER": "off",
		"CEREBRAS_API_KEY": "c-key",
		"GROQ_API_KEY": "g-key",
	}, 1)
	if !reflect.DeepEqual(got, chain) {
		t.Fatalf("got=%v want=%v", got, chain)
	}
}

func TestRecordLatencyUsesEWMA(t *testing.T) {
	state := NewSmartRouterState()
	values := map[string]string{"ATRI_FREE_LATENCY_EWMA_ALPHA": "0.25"}
	state.RecordLatency("groq_gptoss", 1000, values)
	state.RecordLatency("groq_gptoss", 2000, values)
	if got := state.LatencyMS["groq_gptoss"]; !closeEnough(got, 1250) {
		t.Fatalf("ewma=%v want=1250", got)
	}
}

func TestStatusReportsCooldownAndTelemetry(t *testing.T) {
	state := NewSmartRouterState()
	state.LatencyMS["groq_gptoss"] = 900
	state.RequestRatio["groq_gptoss"] = 0.4
	state.CooldownUntil["groq_gptoss"] = 130
	status := state.Status(map[string]string{}, 100)
	groq := status.Providers["groq_gptoss"]
	if !status.Enabled || !closeEnough(groq.CooldownInSeconds, 30) {
		t.Fatalf("status=%+v", status)
	}
	if groq.EWMAMS == nil || !closeEnough(*groq.EWMAMS, 900) {
		t.Fatalf("ewma=%v", groq.EWMAMS)
	}
	if groq.RequestRemainingRatio == nil || !closeEnough(*groq.RequestRemainingRatio, 0.4) {
		t.Fatalf("request ratio=%v", groq.RequestRemainingRatio)
	}
}
