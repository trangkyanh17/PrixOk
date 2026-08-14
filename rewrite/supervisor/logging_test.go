package main

import (
	"strings"
	"testing"
	"time"
)

func TestConfigureLogTimezoneExplicit(t *testing.T) {
	oldLocal := time.Local
	defer func() { time.Local = oldLocal }()

	t.Setenv("ATRI_LOG_TIMEZONE", "Asia/Ho_Chi_Minh")
	t.Setenv("TZ", "UTC")
	if err := configureLogTimezone(); err != nil {
		t.Fatal(err)
	}
	_, offset := time.Date(2026, time.August, 14, 19, 0, 0, 0, time.Local).Zone()
	if offset != 7*60*60 {
		t.Fatalf("offset=%d", offset)
	}
}

func TestConfigureLogTimezoneFallsBackToTZ(t *testing.T) {
	oldLocal := time.Local
	defer func() { time.Local = oldLocal }()

	t.Setenv("ATRI_LOG_TIMEZONE", "")
	t.Setenv("TZ", "Asia/Ho_Chi_Minh")
	if err := configureLogTimezone(); err != nil {
		t.Fatal(err)
	}
	if time.Local.String() != "Asia/Ho_Chi_Minh" {
		t.Fatalf("location=%q", time.Local.String())
	}
}

func TestConfigureLogTimezoneRejectsInvalidLocation(t *testing.T) {
	oldLocal := time.Local
	defer func() { time.Local = oldLocal }()

	t.Setenv("ATRI_LOG_TIMEZONE", "Invalid/Timezone")
	if err := configureLogTimezone(); err == nil || !strings.Contains(err.Error(), "Invalid/Timezone") {
		t.Fatalf("err=%v", err)
	}
	if time.Local != oldLocal {
		t.Fatal("invalid timezone mutated time.Local")
	}
}
