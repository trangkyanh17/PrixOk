package main

import (
	"os"
	"strconv"
	"time"
)

type config struct {
	BotSession           string
	BotLauncher          string
	LocalHealth          string
	BrowserEnsure        string
	NetworkState         string
	LoopInterval         time.Duration
	NetworkCheckInterval time.Duration
	NetworkProbeTimeout  time.Duration
}

func loadConfig() config {
	return config{
		BotSession:           envString("ATRI_BOT_SESSION", "prixok-bot"),
		BotLauncher:          envString("ATRI_BOT_LAUNCHER", os.ExpandEnv("$HOME/prixok-bot.sh")),
		LocalHealth:          envString("ATRI_LOCAL_HEALTH", os.ExpandEnv("$HOME/atri-production-local-health.sh")),
		BrowserEnsure:        envString("ATRI_BROWSER_ENSURE", os.ExpandEnv("$HOME/atri-production-browser-ensure.sh")),
		NetworkState:         envString("ATRI_NETWORK_STATE", os.ExpandEnv("$HOME/atri-production-network-state.sh")),
		LoopInterval:         envDurationSeconds("ATRI_WATCHDOG_INTERVAL", 30),
		NetworkCheckInterval: envDurationSeconds("ATRI_NETWORK_INTERVAL", 180),
		NetworkProbeTimeout:  envDurationSeconds("ATRI_NETWORK_TIMEOUT", 8),
	}
}

func envString(name, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}

func envDurationSeconds(name string, fallback int) time.Duration {
	value, err := strconv.Atoi(os.Getenv(name))
	if err != nil || value <= 0 {
		value = fallback
	}
	return time.Duration(value) * time.Second
}
