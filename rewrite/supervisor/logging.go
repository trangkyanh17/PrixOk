package main

import (
	"fmt"
	"os"
	"strings"
	"time"
	_ "time/tzdata"
)

func configuredLogTimezone() string {
	for _, name := range []string{"ATRI_LOG_TIMEZONE", "TZ"} {
		value := strings.TrimSpace(os.Getenv(name))
		value = strings.TrimPrefix(value, ":")
		if value != "" {
			return value
		}
	}
	return ""
}

func configureLogTimezone() error {
	name := configuredLogTimezone()
	if name == "" {
		return nil
	}
	location, err := time.LoadLocation(name)
	if err != nil {
		return fmt.Errorf("load timezone %q: %w", name, err)
	}
	time.Local = location
	return nil
}
