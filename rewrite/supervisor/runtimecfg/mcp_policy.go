package runtimecfg

import (
	"fmt"
	"sort"
	"strings"
)

var MCPPluginNames = []string{
	"serena",
	"context7",
	"github",
	"semgrep",
	"sentry",
	"chrome-devtools",
}

var mcpPluginHints = map[string][]string{
	"serena": {
		"source", "codebase", "symbol", "reference", "refactor",
		"file", "class", "function",
	},
	"context7": {
		"docs", "documentation", "library", "framework", "api",
		"version", "example",
	},
	"github": {
		"github", "repository", "repo", "issue", "pull request",
		"commit", "actions", "release",
	},
	"semgrep": {
		"security", "vulnerability", "scan", "sast", "secret",
		"unsafe", "cve",
	},
	"sentry": {
		"sentry", "production", "exception", "trace", "event",
		"runtime error", "crash",
	},
	"chrome-devtools": {
		"browser", "chrome", "dom", "console", "network",
		"lighthouse", "performance", "web page",
	},
}

var mcpDirectPluginAliases = []struct {
	Alias  string
	Plugin string
}{
	{"chrome devtools", "chrome-devtools"},
	{"chrome-devtools", "chrome-devtools"},
	{"context 7", "context7"},
	{"context7", "context7"},
	{"github mcp", "github"},
	{"github", "github"},
	{"semgrep", "semgrep"},
	{"serena", "serena"},
	{"sentry", "sentry"},
}

var mcpSensitivePathMarkers = []string{
	"vertex-service-account.json",
	"rclone.conf",
	"config.py",
	".env",
	"credentials",
	"client_secret",
	"service-account",
	"service_account",
	"private_key",
	"token.json",
}

var mcpBlockedTools = map[string]map[string]bool{
	"sentry": {
		"update_issue":             true,
		"analyze_issue_with_seer": true,
		"search_sentry_tools":     true,
		"execute_sentry_tool":     true,
	},
}

var mcpWriteMarkers = []string{
	"delete",
	"remove",
	"write",
	"create",
	"update",
	"edit",
	"replace",
	"insert",
	"execute_shell",
	"push",
	"merge",
	"upload",
}

type MCPTool struct {
	Plugin      string
	Name        string
	Description string
	InputSchema map[string]any
}

type MCPPolicy struct {
	AllowWrite bool
}

func normalizeMCPPlugin(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}

func MCPPluginKnown(plugin string) bool {
	plugin = normalizeMCPPlugin(plugin)
	for _, name := range MCPPluginNames {
		if plugin == name {
			return true
		}
	}
	return false
}

func MCPExplicitPlugin(query string) string {
	normalized := strings.ToLower(strings.TrimSpace(query))
	normalized = strings.NewReplacer("_", " ", "/", " ").Replace(normalized)
	for _, item := range mcpDirectPluginAliases {
		if strings.Contains(normalized, item.Alias) {
			return item.Plugin
		}
	}
	return ""
}

func MCPSelectedPlugins(query string) []string {
	text := strings.ToLower(query)
	explicit := []string{}
	for _, name := range MCPPluginNames {
		if strings.Contains(text, name) {
			explicit = append(explicit, name)
		}
	}
	if len(explicit) > 0 {
		return explicit[:1]
	}

	selected := []string{}
	for _, name := range MCPPluginNames {
		hints := mcpPluginHints[name]
		for _, hint := range hints {
			if strings.Contains(text, hint) {
				selected = append(selected, name)
				break
			}
		}
	}
	if len(selected) > 3 {
		selected = selected[:3]
	}
	if len(selected) > 0 {
		return selected
	}
	return []string{"serena", "context7", "semgrep"}
}

func mcpResolvePointer(root map[string]any, ref string) any {
	if !strings.HasPrefix(ref, "#/") {
		return nil
	}
	var current any = root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(token, "~1", "/")
		token = strings.ReplaceAll(token, "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil
		}
		value, exists := object[token]
		if !exists {
			return nil
		}
		current = value
	}
	return current
}

func mcpSanitizeValue(root map[string]any, value any, stack map[string]bool) any {
	switch typed := value.(type) {
	case nil, bool, int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64, float32, float64:
		return typed
	case string:
		return strings.ReplaceAll(typed, "#/$defs/", "schema:")
	case []string:
		result := make([]any, 0, len(typed))
		for _, item := range typed {
			result = append(result, mcpSanitizeValue(root, item, stack))
		}
		return result
	case []any:
		result := make([]any, 0, len(typed))
		for _, item := range typed {
			result = append(result, mcpSanitizeValue(root, item, stack))
		}
		return result
	case map[string]any:
		if ref, ok := typed["$ref"].(string); ok && strings.HasPrefix(ref, "#/") && !stack[ref] {
			if target, ok := mcpResolvePointer(root, ref).(map[string]any); ok {
				nextStack := make(map[string]bool, len(stack)+1)
				for key, enabled := range stack {
					nextStack[key] = enabled
				}
				nextStack[ref] = true
				merged := cloneAnyMap(target)
				for key, item := range typed {
					if key == "$ref" {
						continue
					}
					merged[key] = item
				}
				return mcpSanitizeValue(root, merged, nextStack)
			}
		}

		result := map[string]any{}
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if strings.HasPrefix(key, "$") {
				continue
			}
			result[key] = mcpSanitizeValue(root, typed[key], stack)
		}
		return result
	default:
		return fmt.Sprint(value)
	}
}

func MCPSanitizeSchema(schema map[string]any) map[string]any {
	if schema == nil {
		return map[string]any{}
	}
	cleaned, ok := mcpSanitizeValue(schema, schema, map[string]bool{}).(map[string]any)
	if !ok || cleaned == nil {
		return map[string]any{}
	}
	return cleaned
}

func MCPContainsSensitivePath(value any) bool {
	switch typed := value.(type) {
	case map[string]any:
		for key, item := range typed {
			if MCPContainsSensitivePath(key) || MCPContainsSensitivePath(item) {
				return true
			}
		}
		return false
	case []any:
		for _, item := range typed {
			if MCPContainsSensitivePath(item) {
				return true
			}
		}
		return false
	case []string:
		for _, item := range typed {
			if MCPContainsSensitivePath(item) {
				return true
			}
		}
		return false
	default:
		text := strings.ToLower(fmt.Sprint(value))
		for _, marker := range mcpSensitivePathMarkers {
			if strings.Contains(text, marker) {
				return true
			}
		}
		return false
	}
}

func (policy MCPPolicy) ToolAllowed(plugin string, tool string) (bool, string) {
	plugin = normalizeMCPPlugin(plugin)
	tool = strings.TrimSpace(tool)
	if !MCPPluginKnown(plugin) {
		return false, "unknown plugin"
	}
	if blocked := mcpBlockedTools[plugin]; blocked != nil && blocked[tool] {
		return false, "tool blocked by read-only policy"
	}
	if policy.AllowWrite {
		return true, ""
	}
	lower := strings.ToLower(tool)
	for _, marker := range mcpWriteMarkers {
		if strings.Contains(lower, marker) {
			return false, "write-capable MCP tool blocked"
		}
	}
	return true, ""
}

func MCPScoreTools(query string, tools []MCPTool, limit int) []MCPTool {
	if limit <= 0 {
		limit = 12
	}
	if limit > 30 {
		limit = 30
	}
	terms := []string{}
	for _, term := range strings.Fields(strings.ToLower(query)) {
		if len([]rune(term)) >= 2 {
			terms = append(terms, term)
		}
	}
	type scoredTool struct {
		Score int
		Index int
		Tool  MCPTool
	}
	scored := []scoredTool{}
	for index, tool := range tools {
		haystack := strings.ToLower(tool.Name + " " + tool.Description)
		score := 0
		for _, term := range terms {
			if strings.Contains(haystack, term) {
				score++
			}
		}
		if score > 0 {
			scored = append(scored, scoredTool{Score: score, Index: index, Tool: tool})
		}
	}
	sort.SliceStable(scored, func(i, j int) bool {
		if scored[i].Score == scored[j].Score {
			return scored[i].Index < scored[j].Index
		}
		return scored[i].Score > scored[j].Score
	})
	result := make([]MCPTool, 0, minInt(limit, len(scored)))
	for _, item := range scored {
		tool := item.Tool
		tool.Plugin = normalizeMCPPlugin(tool.Plugin)
		tool.Name = strings.TrimSpace(tool.Name)
		tool.Description = strings.TrimSpace(tool.Description)
		tool.InputSchema = MCPSanitizeSchema(tool.InputSchema)
		result = append(result, tool)
		if len(result) == limit {
			break
		}
	}
	return result
}

func minInt(left int, right int) int {
	if left < right {
		return left
	}
	return right
}

func MCPCompactFastpathArgs(schema map[string]any) string {
	schema = MCPSanitizeSchema(schema)
	properties, _ := schema["properties"].(map[string]any)
	if len(properties) == 0 {
		return "none"
	}
	required := map[string]bool{}
	switch values := schema["required"].(type) {
	case []any:
		for _, value := range values {
			required[fmt.Sprint(value)] = true
		}
	case []string:
		for _, value := range values {
			required[value] = true
		}
	}

	names := make([]string, 0, len(properties))
	for name := range properties {
		names = append(names, name)
	}
	sort.Strings(names)
	parts := make([]string, 0, len(names))
	for _, name := range names {
		metadata, _ := properties[name].(map[string]any)
		valueType := strings.TrimSpace(fmt.Sprint(metadata["type"]))
		if valueType == "" || valueType == "<nil>" {
			valueType = "any"
		}
		if values, ok := metadata["enum"].([]any); ok && len(values) > 0 {
			choices := make([]string, 0, minInt(len(values), 8))
			for _, value := range values {
				choice := fmt.Sprint(value)
				if len([]rune(choice)) > 40 {
					choice = string([]rune(choice)[:40])
				}
				choices = append(choices, choice)
				if len(choices) == 8 {
					break
				}
			}
			valueType += "[" + strings.Join(choices, ",") + "]"
		}
		suffix := ":optional"
		if required[name] {
			suffix = ":required"
		}
		parts = append(parts, fmt.Sprintf("%s<%s>%s", name, valueType, suffix))
	}
	if len(parts) == 0 {
		return "none"
	}
	return strings.Join(parts, ", ")
}

func MCPBuildDirectCatalog(
	query string,
	tools []MCPTool,
	policy MCPPolicy,
	limit int,
) map[string]any {
	plugin := MCPExplicitPlugin(query)
	if plugin == "" {
		return map[string]any{
			"ok":         false,
			"plugin":     "",
			"context":    "",
			"tool_count": 0,
		}
	}
	if limit <= 0 {
		limit = 10
	}
	if limit > 16 {
		limit = 16
	}

	safe := []MCPTool{}
	for _, tool := range tools {
		if normalizeMCPPlugin(tool.Plugin) != plugin || strings.TrimSpace(tool.Name) == "" {
			continue
		}
		if allowed, _ := policy.ToolAllowed(plugin, tool.Name); !allowed {
			continue
		}
		tool.InputSchema = MCPSanitizeSchema(tool.InputSchema)
		safe = append(safe, tool)
		if len(safe) == limit {
			break
		}
	}
	if len(safe) == 0 {
		return map[string]any{
			"ok":         false,
			"plugin":     plugin,
			"context":    "",
			"tool_count": 0,
			"error":      "No safe direct-call tools found",
		}
	}

	lines := []string{
		"DIRECT MCP TOOL CATALOG",
		"Plugin explicitly requested: " + plugin,
		"The catalog has already been resolved by the backend. DO NOT call code_plugin_search for this plugin. Call code_plugin_call directly with plugin=" + plugin + " and the appropriate tool.",
		"Available safe tools:",
	}
	for _, tool := range safe {
		description := strings.Join(strings.Fields(tool.Description), " ")
		if len([]rune(description)) > 220 {
			description = string([]rune(description)[:220])
		}
		line := fmt.Sprintf("- %s | args: %s", tool.Name, MCPCompactFastpathArgs(tool.InputSchema))
		if description != "" {
			line += " | " + description
		}
		lines = append(lines, line)
	}
	return map[string]any{
		"ok":         true,
		"plugin":     plugin,
		"tool_count": len(safe),
		"context":    strings.Join(lines, "\n"),
	}
}
