package runtimecfg

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
	"sync"
	"time"
)

type MCPAvailabilityBackend interface {
	PluginStatus(context.Context, string) (bool, string)
}

var mcpPluginDescriptions = map[string]string{
	"serena":          "Source-code navigation and semantic codebase tools.",
	"context7":        "Current library and framework documentation.",
	"github":          "GitHub repository, issue, pull request and Actions tools.",
	"semgrep":         "Static analysis and code security scanning.",
	"sentry":          "Read-only production error and trace inspection.",
	"chrome-devtools": "Browser, DOM, console, network and performance inspection.",
}

func (runtime *MCPRuntime) pluginStatus(
	ctx context.Context,
	plugin string,
) (bool, string) {
	if runtime == nil || runtime.Backend == nil {
		return false, "MCP backend is not configured"
	}
	plugin = normalizeMCPPlugin(plugin)
	if !MCPPluginKnown(plugin) {
		return false, "unknown plugin"
	}
	if backend, ok := runtime.Backend.(MCPAvailabilityBackend); ok {
		return backend.PluginStatus(ctx, plugin)
	}
	return true, "backend configured"
}

func (runtime *MCPRuntime) Status(
	ctx context.Context,
	plugin string,
	probe bool,
) map[string]any {
	plugin = normalizeMCPPlugin(plugin)
	names := MCPPluginNames
	if plugin != "" {
		names = []string{plugin}
	}

	plugins := map[string]any{}
	for _, name := range names {
		if !MCPPluginKnown(name) {
			plugins[name] = map[string]any{
				"ready":       false,
				"reason":      "unknown plugin",
				"description": "",
			}
			continue
		}

		ready, reason := runtime.pluginStatus(ctx, name)
		item := map[string]any{
			"ready":       ready,
			"reason":      reason,
			"description": mcpPluginDescriptions[name],
		}
		if ready && probe {
			tools, err := runtime.listTools(ctx, name)
			if err != nil {
				item["probe"] = "failed"
				item["error"] = fmt.Sprintf("%T: %v", err, err)
			} else {
				item["probe"] = "ok"
				item["tool_count"] = len(runtime.safeTools(tools))
			}
		}
		plugins[name] = item
	}

	return map[string]any{
		"ok":            true,
		"write_enabled": runtime != nil && runtime.Policy.AllowWrite,
		"plugins":       plugins,
	}
}

func mcpBatchSteps(value any) ([]map[string]any, error) {
	switch steps := value.(type) {
	case nil:
		return nil, nil
	case []map[string]any:
		result := make([]map[string]any, 0, len(steps))
		for _, step := range steps {
			result = append(result, cloneAnyMap(step))
		}
		return result, nil
	case []any:
		result := make([]map[string]any, 0, len(steps))
		for _, raw := range steps {
			step, ok := raw.(map[string]any)
			if !ok {
				result = append(result, map[string]any{"__invalid_step": raw})
				continue
			}
			result = append(result, cloneAnyMap(step))
		}
		return result, nil
	case string:
		text := strings.TrimSpace(steps)
		if text == "" {
			return nil, nil
		}
		var decoded []any
		if err := json.Unmarshal([]byte(text), &decoded); err != nil {
			return nil, fmt.Errorf("steps_json invalid: %w", err)
		}
		return mcpBatchSteps(decoded)
	default:
		return nil, fmt.Errorf("steps/steps_json must decode to an array")
	}
}

func (runtime *MCPRuntime) Batch(
	ctx context.Context,
	plugin string,
	steps []map[string]any,
	stopOnError bool,
) map[string]any {
	plugin = normalizeMCPPlugin(plugin)
	if !MCPPluginKnown(plugin) {
		return map[string]any{"ok": false, "error": "Unknown plugin: " + plugin}
	}
	ready, reason := runtime.pluginStatus(ctx, plugin)
	if !ready {
		return map[string]any{"ok": false, "plugin": plugin, "error": reason}
	}
	if len(steps) == 0 {
		return map[string]any{"ok": false, "error": "steps must be a non-empty list"}
	}
	if len(steps) > 10 {
		return map[string]any{"ok": false, "error": "Maximum 10 batch steps"}
	}

	results := []any{}
	allOK := true
	for index, step := range steps {
		stepNumber := index + 1
		if _, invalid := step["__invalid_step"]; invalid {
			item := map[string]any{
				"ok":    false,
				"step":  stepNumber,
				"error": "step must be an object",
			}
			results = append(results, item)
			allOK = false
			if stopOnError {
				break
			}
			continue
		}

		tool := mcpArgumentString(step["tool"])
		arguments, ok := step["arguments"].(map[string]any)
		if step["arguments"] != nil && !ok {
			item := map[string]any{
				"ok":    false,
				"step":  stepNumber,
				"tool":  tool,
				"error": "arguments must be an object",
			}
			results = append(results, item)
			allOK = false
			if stopOnError {
				break
			}
			continue
		}
		if arguments == nil {
			arguments = map[string]any{}
		}

		callResult := runtime.Call(ctx, plugin, tool, arguments)
		item := cloneAnyMap(callResult)
		item["step"] = stepNumber
		item["tool"] = tool
		results = append(results, item)
		if item["ok"] != true {
			allOK = false
			if stopOnError {
				break
			}
		}
	}

	return map[string]any{
		"ok":      len(results) > 0 && allOK,
		"plugin":  plugin,
		"results": results,
	}
}

var context7LibraryIDPattern = regexp.MustCompile(
	`(?m)(^|[^A-Za-z0-9_.-])(/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)`,
)

type mcpContext7CacheEntry struct {
	At        time.Time
	LibraryID string
}

type MCPContext7Fastpath struct {
	Runtime  *MCPRuntime
	CacheTTL time.Duration
	Now      func() time.Time

	mu    sync.Mutex
	cache map[string]mcpContext7CacheEntry
}

func (fastpath *MCPContext7Fastpath) now() time.Time {
	if fastpath != nil && fastpath.Now != nil {
		return fastpath.Now()
	}
	return time.Now()
}

func (fastpath *MCPContext7Fastpath) cacheTTL() time.Duration {
	if fastpath == nil || fastpath.CacheTTL <= 0 {
		return time.Hour
	}
	return fastpath.CacheTTL
}

func extractContext7LibraryID(value any) string {
	candidates := []string{}
	var walk func(any)
	walk = func(item any) {
		switch typed := item.(type) {
		case map[string]any:
			for key, child := range typed {
				folded := strings.ToLower(strings.TrimSpace(key))
				if folded == "libraryid" || folded == "library_id" || folded == "id" {
					if text, ok := child.(string); ok && strings.HasPrefix(strings.TrimSpace(text), "/") {
						candidates = append([]string{strings.TrimSpace(text)}, candidates...)
					}
				}
				walk(child)
			}
		case []any:
			for _, child := range typed {
				walk(child)
			}
		case []string:
			for _, child := range typed {
				walk(child)
			}
		case string:
			for _, match := range context7LibraryIDPattern.FindAllStringSubmatch(typed, -1) {
				if len(match) >= 3 {
					candidates = append(candidates, match[2])
				}
			}
		}
	}
	walk(value)
	if len(candidates) == 0 {
		return ""
	}
	return candidates[0]
}

func (fastpath *MCPContext7Fastpath) Query(
	ctx context.Context,
	library string,
	query string,
) map[string]any {
	library = strings.TrimSpace(library)
	query = strings.TrimSpace(query)
	if library == "" {
		return map[string]any{"ok": false, "error": "library is required"}
	}
	if query == "" {
		return map[string]any{"ok": false, "error": "query is required"}
	}
	if fastpath == nil || fastpath.Runtime == nil {
		return map[string]any{"ok": false, "plugin": "context7", "error": "MCP runtime is not configured"}
	}
	ready, reason := fastpath.Runtime.pluginStatus(ctx, "context7")
	if !ready {
		return map[string]any{"ok": false, "plugin": "context7", "error": reason}
	}

	cacheKey := strings.ToLower(library)
	now := fastpath.now()
	fastpath.mu.Lock()
	if fastpath.cache == nil {
		fastpath.cache = map[string]mcpContext7CacheEntry{}
	}
	cached := fastpath.cache[cacheKey]
	cacheHit := cached.LibraryID != "" && now.Sub(cached.At) < fastpath.cacheTTL()
	libraryID := cached.LibraryID
	if !cacheHit {
		libraryID = ""
	}
	fastpath.mu.Unlock()

	if libraryID == "" {
		resolved := fastpath.Runtime.Call(
			ctx,
			"context7",
			"resolve-library-id",
			map[string]any{
				"libraryName": library,
				"query":       query,
			},
		)
		if resolved["ok"] != true {
			return map[string]any{
				"ok":      false,
				"plugin":  "context7",
				"library": library,
				"error":   resolved["error"],
			}
		}
		libraryID = extractContext7LibraryID(resolved)
		if libraryID == "" {
			return map[string]any{
				"ok":      false,
				"plugin":  "context7",
				"library": library,
				"error":   "Context7 did not return a library ID",
			}
		}
		fastpath.mu.Lock()
		fastpath.cache[cacheKey] = mcpContext7CacheEntry{At: now, LibraryID: libraryID}
		fastpath.mu.Unlock()
	}

	docs := fastpath.Runtime.Call(
		ctx,
		"context7",
		"query-docs",
		map[string]any{
			"libraryId": libraryID,
			"query":     query,
		},
	)
	return map[string]any{
		"ok":         docs["ok"] == true,
		"plugin":     "context7",
		"library":    library,
		"library_id": libraryID,
		"cache_hit":  cacheHit,
		"content":    docs["content"],
		"structured": docs["structured"],
		"error":      docs["error"],
	}
}

func CodePluginStatusDeclaration() map[string]any {
	return map[string]any{
		"name":        "code_plugin_status",
		"description": "Kiểm tra trạng thái các coding MCP plugin.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"plugin": map[string]any{"type": "string", "enum": []any{"serena", "context7", "github", "semgrep", "sentry", "chrome-devtools"}},
				"probe":  map[string]any{"type": "boolean"},
			},
		},
	}
}

func CodePluginBatchDeclaration() map[string]any {
	return map[string]any{
		"name":        "code_plugin_batch",
		"description": "Gọi nhiều MCP tools tuần tự. Backend persistent có thể giữ cùng plugin session giữa các bước.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"plugin":        map[string]any{"type": "string", "enum": []any{"serena", "context7", "github", "semgrep", "sentry", "chrome-devtools"}},
				"steps_json":    map[string]any{"type": "string"},
				"stop_on_error": map[string]any{"type": "boolean"},
			},
			"required": []any{"plugin", "steps_json"},
		},
	}
}

func CodeContext7DocsDeclaration() map[string]any {
	return map[string]any{
		"name":        "code_context7_docs",
		"description": "Tra cứu docs/API hiện hành của một library/package bằng Context7, tự resolve library ID rồi query docs.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"library": map[string]any{"type": "string"},
				"query":   map[string]any{"type": "string"},
			},
			"required": []any{"library", "query"},
		},
	}
}

func (runtime *MCPRuntime) FeatureTools() []RegisteredTool {
	context7 := &MCPContext7Fastpath{Runtime: runtime}
	return []RegisteredTool{
		{
			Name:        "code_context7_docs",
			Declaration: CodeContext7DocsDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Modes:       []string{"code"},
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return context7.Query(
					ctx,
					mcpArgumentString(arguments["library"]),
					mcpArgumentString(arguments["query"]),
				), nil
			},
		},
		{
			Name:        "code_plugin_batch",
			Declaration: CodePluginBatchDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Modes:       []string{"code"},
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				stepsValue := arguments["steps"]
				if stepsValue == nil {
					stepsValue = arguments["steps_json"]
				}
				steps, err := mcpBatchSteps(stepsValue)
				if err != nil {
					return map[string]any{"ok": false, "error": err.Error()}, nil
				}
				stopOnError, ok := arguments["stop_on_error"].(bool)
				if !ok {
					stopOnError = true
				}
				return runtime.Batch(
					ctx,
					mcpArgumentString(arguments["plugin"]),
					steps,
					stopOnError,
				), nil
			},
		},
		{
			Name:        "code_plugin_status",
			Declaration: CodePluginStatusDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Modes:       []string{"code"},
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				probe, _ := arguments["probe"].(bool)
				return runtime.Status(
					ctx,
					mcpArgumentString(arguments["plugin"]),
					probe,
				), nil
			},
		},
	}
}

func RegisterMCPFeatureTools(registry *ToolRegistry, runtime *MCPRuntime) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	if runtime == nil || runtime.Backend == nil {
		return fmt.Errorf("MCP runtime is not configured")
	}
	for _, tool := range runtime.FeatureTools() {
		if err := registry.Register(tool); err != nil {
			return err
		}
	}
	return nil
}

func RegisterAllMCPTools(registry *ToolRegistry, runtime *MCPRuntime) error {
	if err := RegisterMCPTools(registry, runtime); err != nil {
		return err
	}
	return RegisterMCPFeatureTools(registry, runtime)
}
