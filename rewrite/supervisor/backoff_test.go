package main

import (
	"testing"
	"time"
)

func TestRepairDelayMatchesProductionPolicy(t *testing.T) {
	want := []time.Duration{
		30 * time.Second,
		60 * time.Second,
		120 * time.Second,
		300 * time.Second,
		600 * time.Second,
		600 * time.Second,
	}
	for i, expected := range want {
		if got := repairDelay(i + 1); got != expected {
			t.Fatalf("failure %d: got %s want %s", i+1, got, expected)
		}
	}
}

func TestRepairBackoffLifecycle(t *testing.T) {
	now := time.Unix(1_000, 0)
	var state repairBackoff
	if !state.ready(now) {
		t.Fatal("fresh state should be ready")
	}
	if got := state.fail(now); got != 30*time.Second {
		t.Fatalf("first delay = %s", got)
	}
	if state.ready(now.Add(29 * time.Second)) {
		t.Fatal("state became ready too early")
	}
	if !state.ready(now.Add(30 * time.Second)) {
		t.Fatal("state should be ready at deadline")
	}
	state.reset()
	if !state.ready(now) || state.failures != 0 {
		t.Fatal("reset failed")
	}
}
