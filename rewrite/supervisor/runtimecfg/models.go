package runtimecfg

import "strings"

type ModelSpec struct {
	Model           string
	DefaultThinking string
	AllowedThinking []string
}

var specs = []ModelSpec{
	{Model: "gemini-3-flash-preview", DefaultThinking: "high", AllowedThinking: []string{"minimal", "low", "medium", "high"}},
	{Model: "gemini-3.1-pro-preview", DefaultThinking: "high", AllowedThinking: []string{"low", "medium", "high"}},
	{Model: "gemini-3.6-flash", DefaultThinking: "medium", AllowedThinking: []string{"minimal", "low", "medium", "high"}},
	{Model: "gemini-3.5-flash", DefaultThinking: "medium", AllowedThinking: []string{"minimal", "low", "medium", "high"}},
	{Model: "gemini-3.5-flash-lite", DefaultThinking: "minimal", AllowedThinking: []string{"minimal", "low", "medium", "high"}},
	{Model: "gemini-3.1-flash-lite", DefaultThinking: "minimal", AllowedThinking: []string{"minimal", "low", "medium", "high"}},
}

var aliases = map[string]string{
	"3flash":   "gemini-3-flash-preview",
	"3.0flash": "gemini-3-flash-preview",
	"flash3":   "gemini-3-flash-preview",
	"pro":      "gemini-3.1-pro-preview",
	"flash":    "gemini-3.6-flash",
	"36flash":  "gemini-3.6-flash",
	"3.6flash": "gemini-3.6-flash",
	"35flash":  "gemini-3.5-flash",
	"3.5flash": "gemini-3.5-flash",
	"lite":     "gemini-3.5-flash-lite",
	"31lite":   "gemini-3.1-flash-lite",
}

func Specs() []ModelSpec {
	out := make([]ModelSpec, len(specs))
	copy(out, specs)
	return out
}

func ResolveModel(value string) (ModelSpec, bool) {
	name := strings.ToLower(strings.TrimSpace(value))
	if alias, ok := aliases[name]; ok {
		name = alias
	}
	for _, spec := range specs {
		if spec.Model == name {
			return spec, true
		}
	}
	return ModelSpec{}, false
}

func ResolveThinking(spec ModelSpec, value string) (string, bool) {
	level := strings.ToLower(strings.TrimSpace(value))
	if level == "" || level == "default" {
		return spec.DefaultThinking, true
	}
	for _, allowed := range spec.AllowedThinking {
		if level == allowed {
			return allowed, true
		}
	}
	return "", false
}
