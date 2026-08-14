package runtimecfg

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
)

const fallbackRuntimeModel = "gemini-3.5-flash-lite"

var runtimeConfigMu sync.Mutex

type RuntimeState struct {
	Model           string   `json:"model"`
	Thinking        string   `json:"thinking"`
	DefaultThinking string   `json:"default_thinking"`
	AllowedThinking []string `json:"allowed_thinking"`
}

func RuntimeStateFor(model, thinking string) RuntimeState {
	spec, ok := ResolveModel(model)
	if !ok {
		spec, _ = ResolveModel(fallbackRuntimeModel)
	}
	level, ok := ResolveThinking(spec, thinking)
	if !ok {
		level = spec.DefaultThinking
	}
	allowed := append([]string(nil), spec.AllowedThinking...)
	return RuntimeState{
		Model:           spec.Model,
		Thinking:        level,
		DefaultThinking: spec.DefaultThinking,
		AllowedThinking: allowed,
	}
}

func SetRuntimeModel(configPath, value string) (RuntimeState, error) {
	spec, ok := ResolveModel(value)
	if !ok {
		return RuntimeState{}, fmt.Errorf("unsupported model: %s", strings.TrimSpace(value))
	}

	runtimeConfigMu.Lock()
	defer runtimeConfigMu.Unlock()
	if err := writeConfigValues(configPath, map[string]string{
		"VERTEX_MODEL":          spec.Model,
		"VERTEX_THINKING_LEVEL": spec.DefaultThinking,
	}); err != nil {
		return RuntimeState{}, err
	}
	return RuntimeStateFor(spec.Model, spec.DefaultThinking), nil
}

func SetRuntimeThinking(configPath, currentModel, value string) (RuntimeState, error) {
	state := RuntimeStateFor(currentModel, "")
	spec, _ := ResolveModel(state.Model)
	level := strings.ToLower(strings.TrimSpace(value))
	if level == "default" {
		level = spec.DefaultThinking
	}
	allowed := false
	for _, candidate := range spec.AllowedThinking {
		if level == candidate {
			allowed = true
			break
		}
	}
	if !allowed {
		return RuntimeState{}, fmt.Errorf(
			"%s supports thinking: %s",
			spec.Model,
			strings.Join(spec.AllowedThinking, ", "),
		)
	}

	runtimeConfigMu.Lock()
	defer runtimeConfigMu.Unlock()
	if err := writeConfigValues(configPath, map[string]string{
		"VERTEX_THINKING_LEVEL": level,
	}); err != nil {
		return RuntimeState{}, err
	}
	return RuntimeStateFor(spec.Model, level), nil
}

func writeConfigValues(configPath string, values map[string]string) error {
	info, err := os.Stat(configPath)
	if err != nil {
		return fmt.Errorf("config file unavailable: %w", err)
	}
	payload, err := os.ReadFile(configPath)
	if err != nil {
		return err
	}
	lines := strings.Split(strings.ReplaceAll(string(payload), "\r\n", "\n"), "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}

	for _, key := range orderedConfigKeys(values) {
		value := values[key]
		pattern := regexp.MustCompile(`^\s*` + regexp.QuoteMeta(key) + `\s*=`)
		replacement := key + " = '" + value + "'"
		updated := make([]string, 0, len(lines)+1)
		replaced := false
		for _, line := range lines {
			if pattern.MatchString(line) {
				if !replaced {
					updated = append(updated, replacement)
					replaced = true
				}
				continue
			}
			updated = append(updated, line)
		}
		if !replaced {
			updated = append(updated, replacement)
		}
		lines = updated
	}

	output := strings.TrimRight(strings.Join(lines, "\n"), "\n") + "\n"
	directory := filepath.Dir(configPath)
	temp, err := os.CreateTemp(directory, "."+filepath.Base(configPath)+".*.tmp")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	cleanup := true
	defer func() {
		if cleanup {
			_ = os.Remove(tempPath)
		}
	}()

	if _, err = temp.WriteString(output); err != nil {
		_ = temp.Close()
		return err
	}
	if err = temp.Sync(); err != nil {
		_ = temp.Close()
		return err
	}
	if err = temp.Chmod(info.Mode().Perm()); err != nil {
		_ = temp.Close()
		return err
	}
	if err = temp.Close(); err != nil {
		return err
	}
	if err = os.Rename(tempPath, configPath); err != nil {
		return err
	}
	cleanup = false

	dirHandle, err := os.Open(directory)
	if err != nil {
		return err
	}
	defer dirHandle.Close()
	return dirHandle.Sync()
}

func orderedConfigKeys(values map[string]string) []string {
	keys := make([]string, 0, len(values))
	for _, key := range []string{"VERTEX_MODEL", "VERTEX_THINKING_LEVEL"} {
		if _, ok := values[key]; ok {
			keys = append(keys, key)
		}
	}
	extra := make([]string, 0, len(values))
	for key := range values {
		if key != "VERTEX_MODEL" && key != "VERTEX_THINKING_LEVEL" {
			extra = append(extra, key)
		}
	}
	sort.Strings(extra)
	return append(keys, extra...)
}
