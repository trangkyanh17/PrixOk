package runtimecfg

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

type MCPCallResult struct {
	Content    []any
	Structured any
	IsError    bool
}

type MCPBackend interface {
	ListTools(context.Context, string) ([]MCPTool, error)
	CallTool(context.Context, string, string, map[string]any) (MCPCallResult, error)
}

type mcpCacheEntry struct {
	At    time.Time
	Tools []MCPTool
}

type MCPRuntime struct {
	Backend  MCPBackend
	Policy   MCPPolicy
	CacheTTL time.Duration
	Now      func() time.Time

	mu    sync.Mutex
	cache map[string]mcpCacheEntry
}

func (runtime *MCPRuntime) now() time.Time {
	if runtime != nil && runtime.Now != nil {
		return runtime.Now()
	}
	return time.Now()
}

func (runtime *MCPRuntime) cacheTTL() time.Duration {
	if runtime == nil || runtime.CacheTTL <= 0 {
		return time.Hour
	}
	if runtime.CacheTTL < 30*time.Second {
		return 30 * time.Second
	}
	return runtime.CacheTTL
}

func cloneMCPTools(input []MCPTool) []MCPTool {
	result := make([]MCPTool, 0, len(input))
	for _, tool := range input {
		result = append(result, MCPTool{
			Plugin:      normalizeMCPPlugin(tool.Plugin),
			Name:        strings.TrimSpace(tool.Name),
			Description: strings.TrimSpace(tool.Description),
			InputSchema: MCPSanitizeSchema(tool.InputSchema),
		})
	}
	return result
}

func (runtime *MCPRuntime) listTools(ctx context.Context, plugin string) ([]MCPTool, error) {
	if runtime == nil || runtime.Backend == nil {
		return nil, fmt.Errorf("MCP backend is not configured")
	}
	plugin = normalizeMCPPlugin(plugin)
	if !MCPPluginKnown(plugin) {
		return nil, fmt.Errorf("unknown plugin: %s", plugin)
	}

	now := runtime.now()
	runtime.mu.Lock()
	if runtime.cache == nil {
		runtime.cache = map[string]mcpCacheEntry{}
	}
	if cached, ok := runtime.cache[plugin]; ok && now.Sub(cached.At) < runtime.cacheTTL() {
		tools := cloneMCPTools(cached.Tools)
		runtime.mu.Unlock()
		return tools, nil
	}
	runtime.mu.Unlock()

	tools, err := runtime.Backend.ListTools(ctx, plugin)
	if err != nil {
		return nil, err
	}
	for index := range tools {
		tools[index].Plugin = plugin
		tools[index].InputSchema = MCPSanitizeSchema(tools[index].InputSchema)
	}
	tools = cloneMCPTools(tools)

	runtime.mu.Lock()
	runtime.cache[plugin] = mcpCacheEntry{At: now, Tools: cloneMCPTools(tools)}
	runtime.mu.Unlock()
	return tools, nil
}

func (runtime *MCPRuntime) Invalidate(plugin string) {
	if runtime == nil {
		return
	}
	runtime.mu.Lock()
	defer runtime.mu.Unlock()
	if plugin = normalizeMCPPlugin(plugin); plugin != "" {
		delete(runtime.cache, plugin)
		return
	}
	runtime.cache = map[string]mcpCacheEntry{}
}

func mcpToolMap(tool MCPTool) map[string]any {
	return map[string]any{
		"plugin":       normalizeMCPPlugin(tool.Plugin),
		"name":         strings.TrimSpace(tool.Name),
		"description":  strings.TrimSpace(tool.Description),
		"input_schema": MCPSanitizeSchema(tool.InputSchema),
	}
}

func (runtime *MCPRuntime) safeTools(tools []MCPTool) []MCPTool {
	result := []MCPTool{}
	for _, tool := range tools {
		if allowed, _ := runtime.Policy.ToolAllowed(tool.Plugin, tool.Name); !allowed {
			continue
		}
		result = append(result, tool)
	}
	return result
}

func (runtime *MCPRuntime) Search(
	ctx context.Context,
	query string,
	plugin string,
	limit int,
) map[string]any {
	query = strings.TrimSpace(query)
	plugin = normalizeMCPPlugin(plugin)
	if limit <= 0 {
		limit = 12
	}
	if limit > 30 {
		limit = 30
	}

	names := []string{}
	if plugin != "" {
		if !MCPPluginKnown(plugin) {
			return map[string]any{
				"ok":     false,
				"query":  query,
				"tools":  []any{},
				"errors": map[string]any{plugin: "unknown plugin"},
			}
		}
		names = []string{plugin}
	} else {
		names = MCPSelectedPlugins(query)
	}

	type loadResult struct {
		Plugin string
		Tools  []MCPTool
		Err    error
	}
	results := make(chan loadResult, len(names))
	var wait sync.WaitGroup
	for _, name := range names {
		name := name
		wait.Add(1)
		go func() {
			defer wait.Done()
			tools, err := runtime.listTools(ctx, name)
			results <- loadResult{Plugin: name, Tools: tools, Err: err}
		}()
	}
	wait.Wait()
	close(results)

	loaded := make([]loadResult, 0, len(names))
	for item := range results {
		loaded = append(loaded, item)
	}
	sort.SliceStable(loaded, func(i, j int) bool {
		left := indexOfString(names, loaded[i].Plugin)
		right := indexOfString(names, loaded[j].Plugin)
		return left < right
	})

	found := []MCPTool{}
	errorsByPlugin := map[string]any{}
	fallbackPlugins := []any{}
	for _, item := range loaded {
		if item.Err != nil {
			errorsByPlugin[item.Plugin] = fmt.Sprintf("%T: %v", item.Err, item.Err)
			continue
		}
		safe := runtime.safeTools(item.Tools)
		scored := MCPScoreTools(query, safe, limit)
		if len(scored) > 0 {
			found = append(found, scored...)
			continue
		}
		if len(names) == 1 {
			found = append(found, safe...)
			fallbackPlugins = append(fallbackPlugins, item.Plugin)
		}
	}
	if len(found) > limit {
		found = found[:limit]
	}
	toolMaps := make([]any, 0, len(found))
	for _, tool := range found {
		toolMaps = append(toolMaps, mcpToolMap(tool))
	}
	return map[string]any{
		"ok":               len(found) > 0,
		"query":            query,
		"tools":            toolMaps,
		"errors":           errorsByPlugin,
		"fallback_plugins": fallbackPlugins,
	}
}

func indexOfString(values []string, target string) int {
	for index, value := range values {
		if value == target {
			return index
		}
	}
	return len(values)
}

func sanitizeMCPResult(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		result := map[string]any{}
		for key, item := range typed {
			if strings.HasPrefix(key, "$") {
				continue
			}
			result[key] = sanitizeMCPResult(item)
		}
		return result
	case []any:
		result := make([]any, 0, len(typed))
		for _, item := range typed {
			result = append(result, sanitizeMCPResult(item))
		}
		return result
	case []string:
		result := make([]any, 0, len(typed))
		for _, item := range typed {
			result = append(result, sanitizeMCPResult(item))
		}
		return result
	case string:
		return strings.ReplaceAll(typed, "#/$defs/", "schema:")
	default:
		return typed
	}
}

func (runtime *MCPRuntime) Call(
	ctx context.Context,
	plugin string,
	tool string,
	arguments map[string]any,
) map[string]any {
	if runtime == nil || runtime.Backend == nil {
		return map[string]any{"ok": false, "error": "MCP backend is not configured"}
	}
	plugin = normalizeMCPPlugin(plugin)
	tool = strings.TrimSpace(tool)
	if !MCPPluginKnown(plugin) {
		return map[string]any{"ok": false, "error": "Unknown plugin: " + plugin}
	}
	if allowed, reason := runtime.Policy.ToolAllowed(plugin, tool); !allowed {
		return map[string]any{"ok": false, "error": reason + ": " + plugin + "/" + tool}
	}
	if arguments == nil {
		arguments = map[string]any{}
	}
	if MCPContainsSensitivePath(arguments) {
		return map[string]any{
			"ok":    false,
			"error": "Access to sensitive credential/config paths is blocked.",
		}
	}

	result, err := runtime.Backend.CallTool(ctx, plugin, tool, cloneAnyMap(arguments))
	if err != nil {
		return map[string]any{"ok": false, "error": fmt.Sprintf("%T: %v", err, err)}
	}
	return map[string]any{
		"ok":         !result.IsError,
		"content":    sanitizeMCPResult(result.Content),
		"structured": sanitizeMCPResult(result.Structured),
	}
}

func (runtime *MCPRuntime) DirectFastpath(
	ctx context.Context,
	query string,
	limit int,
) map[string]any {
	plugin := MCPExplicitPlugin(query)
	if plugin == "" {
		return MCPBuildDirectCatalog(query, nil, runtime.Policy, limit)
	}
	tools, err := runtime.listTools(ctx, plugin)
	if err != nil {
		return map[string]any{
			"ok":         false,
			"plugin":     plugin,
			"context":    "",
			"tool_count": 0,
			"error":      err.Error(),
		}
	}
	return MCPBuildDirectCatalog(query, tools, runtime.Policy, limit)
}

func CodePluginSearchDeclaration() map[string]any {
	return map[string]any{
		"name":        "code_plugin_search",
		"description": "Discover read-only coding MCP tools from Serena, Context7, GitHub, Semgrep, Sentry and Chrome DevTools.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query":  map[string]any{"type": "string"},
				"plugin": map[string]any{"type": "string", "enum": []any{"serena", "context7", "github", "semgrep", "sentry", "chrome-devtools"}},
				"limit":  map[string]any{"type": "integer", "minimum": 1, "maximum": 30},
			},
			"required": []any{"query"},
		},
	}
}

func CodePluginCallDeclaration() map[string]any {
	return map[string]any{
		"name":        "code_plugin_call",
		"description": "Call one discovered MCP coding tool through the read-only policy boundary.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"plugin":    map[string]any{"type": "string", "enum": []any{"serena", "context7", "github", "semgrep", "sentry", "chrome-devtools"}},
				"tool":      map[string]any{"type": "string"},
				"arguments": map[string]any{"type": "object"},
			},
			"required": []any{"plugin", "tool"},
		},
	}
}

func (runtime *MCPRuntime) RegisteredTools() []RegisteredTool {
	return []RegisteredTool{
		{
			Name:        "code_plugin_search",
			Declaration: CodePluginSearchDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Modes:       []string{"code"},
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.Search(
					ctx,
					fmt.Sprint(arguments["query"]),
					fmt.Sprint(arguments["plugin"]),
					googleClampInt(arguments["limit"], 1, 30, 12),
				), nil
			},
		},
		{
			Name:        "code_plugin_call",
			Declaration: CodePluginCallDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Modes:       []string{"code"},
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				callArguments, _ := arguments["arguments"].(map[string]any)
				return runtime.Call(
					ctx,
					fmt.Sprint(arguments["plugin"]),
					fmt.Sprint(arguments["tool"]),
					callArguments,
				), nil
			},
		},
	}
}

func RegisterMCPTools(registry *ToolRegistry, runtime *MCPRuntime) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	if runtime == nil || runtime.Backend == nil {
		return fmt.Errorf("MCP runtime is not configured")
	}
	for _, tool := range runtime.RegisteredTools() {
		if err := registry.Register(tool); err != nil {
			return err
		}
	}
	return nil
}
