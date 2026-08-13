package main

import (
	"testing"
	"time"
)

func TestDurationDefaults(t *testing.T) {
	if got := envDurationSeconds("ATRI_TEST_MISSING", 30); got != 30*time.Second {
		t.Fatalf("got %s", got)
	}
}
