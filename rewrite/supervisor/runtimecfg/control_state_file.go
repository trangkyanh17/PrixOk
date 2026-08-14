package runtimecfg

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

const DefaultProviderControlPath = "/app/atri_data/atri_provider_control.json"

var providerControlFileMu sync.Mutex

func SaveControlState(path string, state ControlState) error {
	providerControlFileMu.Lock()
	defer providerControlFileMu.Unlock()
	return saveControlStateUnlocked(path, state)
}

func saveControlStateUnlocked(path string, state ControlState) error {
	state = NormalizeControlState(state)
	payload, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	payload = append(payload, '\n')

	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return err
	}
	temp, err := os.CreateTemp(directory, ".atri-provider-control-*.json")
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

	if err := temp.Chmod(0o600); err != nil {
		_ = temp.Close()
		return err
	}
	if _, err := temp.Write(payload); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tempPath, path); err != nil {
		return err
	}
	if err := os.Chmod(path, 0o600); err != nil {
		return err
	}
	cleanup = false
	return nil
}

func LoadControlState(path string) (ControlState, error) {
	providerControlFileMu.Lock()
	defer providerControlFileMu.Unlock()

	payload, err := os.ReadFile(path)
	if err != nil {
		if !os.IsNotExist(err) {
			return DefaultControlState(), err
		}
		state := DefaultControlState()
		if err := saveControlStateUnlocked(path, state); err != nil {
			return ControlState{}, err
		}
		return state, nil
	}

	var state ControlState
	if err := json.Unmarshal(payload, &state); err != nil {
		return DefaultControlState(), nil
	}
	return NormalizeControlState(state), nil
}

func CapabilityStatusMap(state CapabilityState) map[ProviderModel]string {
	status := map[ProviderModel]string{}
	for provider, choices := range CandidateModelChoices {
		for _, choice := range choices {
			status[ProviderModel{Provider: provider, Model: choice.Model}] = state.CapabilityModelStatus(provider, choice.Model)
		}
	}
	return status
}

func HealControlState(state ControlState, capabilityState CapabilityState) (ControlState, bool) {
	state = NormalizeControlState(state)
	original, _ := json.Marshal(state)
	defaults := DefaultControlState()
	status := CapabilityStatusMap(capabilityState)

	for _, provider := range providerOrder {
		item := state.Providers[provider]
		fallback := defaults.Providers[provider].Model
		choices := CandidateModelChoices[provider]
		healedModel := HealSelectedModel(provider, item.Model, fallback, choices, status)
		if healedModel != "" {
			item.Model = healedModel
		}
		item.Thinking = HealProviderThinking(provider, item.Model, item.Thinking)
		state.Providers[provider] = item
	}

	mode := strings.ToLower(strings.TrimSpace(state.ProviderMode))
	if containsString(providerOrder, mode) {
		visible := FilterDeadModelChoices(mode, CandidateModelChoices[mode], status)
		if len(visible) == 0 {
			state.ProviderMode = "smart"
		}
	}

	current, _ := json.Marshal(state)
	return state, string(original) != string(current)
}

func HealAndSaveControlState(path string, state ControlState, capabilityState CapabilityState) (ControlState, bool, error) {
	healed, changed := HealControlState(state, capabilityState)
	if !changed {
		return healed, false, nil
	}
	if err := SaveControlState(path, healed); err != nil {
		return ControlState{}, false, err
	}
	return healed, true, nil
}
