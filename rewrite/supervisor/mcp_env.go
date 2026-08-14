package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

const defaultMCPUVLinkMode = "copy"

func mcpPluginsUseUV(plugins []string) bool {
	if len(plugins) == 0 {
		return true
	}
	for _, plugin := range plugins {
		switch strings.ToLower(strings.TrimSpace(plugin)) {
		case "serena", "semgrep":
			return true
		}
	}
	return false
}

func resolveCommandDir(command string) string {
	command = strings.TrimSpace(command)
	if command == "" {
		return ""
	}
	if filepath.IsAbs(command) {
		return filepath.Dir(command)
	}
	if strings.ContainsRune(command, os.PathSeparator) {
		absolute, err := filepath.Abs(command)
		if err == nil {
			return filepath.Dir(absolute)
		}
	}
	if resolved, err := exec.LookPath(command); err == nil {
		return filepath.Dir(resolved)
	}
	return ""
}

func prependPathEntry(current, entry string) string {
	entry = strings.TrimSpace(entry)
	if entry == "" {
		return current
	}
	cleanEntry := filepath.Clean(entry)
	for _, item := range filepath.SplitList(current) {
		if filepath.Clean(item) == cleanEntry {
			return current
		}
	}
	if current == "" {
		return entry
	}
	return entry + string(os.PathListSeparator) + current
}

func defaultMCPUVCacheDir() string {
	if root := strings.TrimSpace(os.Getenv("XDG_CACHE_HOME")); root != "" {
		return filepath.Join(root, "atri-rewrite-v150", "uv")
	}
	if home := strings.TrimSpace(os.Getenv("HOME")); home != "" {
		return filepath.Join(home, ".cache", "atri-rewrite-v150", "uv")
	}
	return filepath.Join(os.TempDir(), "atri-rewrite-v150-uv-cache")
}

func firstEnv(names ...string) string {
	for _, name := range names {
		if value := strings.TrimSpace(os.Getenv(name)); value != "" {
			return value
		}
	}
	return ""
}

func prepareMCPEnvironment(plugins []string) error {
	if !mcpPluginsUseUV(plugins) {
		return nil
	}

	uvx := firstEnv("ATRI_UVX")
	if uvx == "" {
		uvx = "/app/mltbenv/bin/uvx"
	}
	if dir := resolveCommandDir(uvx); dir != "" {
		if err := os.Setenv("PATH", prependPathEntry(os.Getenv("PATH"), dir)); err != nil {
			return fmt.Errorf("prepare MCP PATH: %w", err)
		}
	}

	linkMode := firstEnv("ATRI_MCP_UV_LINK_MODE", "UV_LINK_MODE")
	if linkMode == "" {
		linkMode = defaultMCPUVLinkMode
	}
	if err := os.Setenv("UV_LINK_MODE", linkMode); err != nil {
		return fmt.Errorf("prepare MCP UV_LINK_MODE: %w", err)
	}

	cacheDir := firstEnv("ATRI_MCP_UV_CACHE_DIR", "UV_CACHE_DIR")
	if cacheDir == "" {
		cacheDir = defaultMCPUVCacheDir()
	}
	if err := os.MkdirAll(cacheDir, 0o700); err != nil {
		return fmt.Errorf("prepare MCP uv cache %q: %w", cacheDir, err)
	}
	if err := os.Setenv("UV_CACHE_DIR", cacheDir); err != nil {
		return fmt.Errorf("prepare MCP UV_CACHE_DIR: %w", err)
	}
	return nil
}
