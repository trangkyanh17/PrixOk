package runtimecfg

import "testing"

func TestFreePoolDynamicMaxTokens(t *testing.T) {
	cases := []struct {
		name   string
		values map[string]string
		level  string
		want   int
	}{
		{name: "minimal default", values: map[string]string{}, level: "minimal", want: 512},
		{name: "unknown uses medium", values: map[string]string{}, level: "weird", want: 2048},
		{name: "global cap wins", values: map[string]string{"ATRI_FREE_MAX_TOKENS": "700"}, level: "high", want: 700},
		{name: "level override wins", values: map[string]string{"ATRI_FREE_MAX_TOKENS": "9000", "ATRI_FREE_MAX_TOKENS_HIGH": "4000"}, level: "high", want: 4000},
		{name: "level clamp", values: map[string]string{"ATRI_FREE_MAX_TOKENS_LOW": "99999"}, level: "low", want: 4096},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if got := FreePoolDynamicMaxTokens(test.values, test.level); got != test.want {
				t.Fatalf("got=%d want=%d", got, test.want)
			}
		})
	}
}

func TestFreePoolMaxAttempts(t *testing.T) {
	if got := FreePoolMaxAttempts(map[string]string{}, "smart", "chat"); got != 4 {
		t.Fatalf("smart chat attempts=%d", got)
	}
	if got := FreePoolMaxAttempts(map[string]string{}, "smart", "research"); got != 3 {
		t.Fatalf("research attempts=%d", got)
	}
	if got := FreePoolMaxAttempts(map[string]string{"ATRI_FREE_MAX_ATTEMPTS": "5"}, "smart", "chat"); got != 5 {
		t.Fatalf("configured attempts=%d", got)
	}
}

func TestFreePoolRequestTimeoutSeconds(t *testing.T) {
	if got := FreePoolRequestTimeoutSeconds(map[string]string{}); got != 20 {
		t.Fatalf("default timeout=%d", got)
	}
	if got := FreePoolRequestTimeoutSeconds(map[string]string{"ATRI_FREE_REQUEST_TIMEOUT": "1"}); got != 5 {
		t.Fatalf("minimum timeout=%d", got)
	}
	if got := FreePoolRequestTimeoutSeconds(map[string]string{"ATRI_FREE_REQUEST_TIMEOUT": "90"}); got != 60 {
		t.Fatalf("maximum timeout=%d", got)
	}
}

func TestFreePoolFailureCooldownSeconds(t *testing.T) {
	cases := []struct {
		status    int
		hasStatus bool
		want      float64
	}{
		{401, true, 300},
		{403, true, 300},
		{429, true, 60},
		{503, true, 20},
		{400, true, 10},
		{0, false, 10},
	}
	for _, test := range cases {
		if got := FreePoolFailureCooldownSeconds(test.status, test.hasStatus); got != test.want {
			t.Fatalf("status=%d has=%v got=%v want=%v", test.status, test.hasStatus, got, test.want)
		}
	}
	if got := FreePoolUnexpectedFailureCooldownSeconds(); got != 15 {
		t.Fatalf("unexpected failure cooldown=%v", got)
	}
}
