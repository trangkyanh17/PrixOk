package runtimecfg

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"sync"
)

type ToolPrivacy string

const (
	ToolPrivacyPublic  ToolPrivacy = "public"
	ToolPrivacyPrivate ToolPrivacy = "private"
	ToolPrivacyMixed   ToolPrivacy = "mixed"
)

type ToolContext struct {
	UserID   int64
	ChatID   int64
	ThreadID int64
	Mode     string
	Metadata map[string]any
}

type RegisteredToolExecutor func(
	ctx context.Context,
	toolContext ToolContext,
	arguments map[string]any,
) (any, error)

type RegisteredTool struct {
	Name        string
	Declaration map[string]any
	Privacy     ToolPrivacy
	Modes       []string
	Executor    RegisteredToolExecutor
}

type ToolRegistry struct {
	mu    sync.RWMutex
	tools map[string]RegisteredTool
}

func NewToolRegistry() *ToolRegistry {
	return &ToolRegistry{tools: map[string]RegisteredTool{}}
}

func normalizeToolMode(mode string) string {
	mode = strings.ToLower(strings.TrimSpace(mode))
	if mode == "" {
		return "chat"
	}
	return mode
}

func normalizeToolPrivacy(privacy ToolPrivacy) ToolPrivacy {
	switch privacy {
	case ToolPrivacyPublic, ToolPrivacyPrivate, ToolPrivacyMixed:
		return privacy
	default:
		return ToolPrivacyPrivate
	}
}

func cloneModes(modes []string) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(modes))
	for _, mode := range modes {
		mode = normalizeToolMode(mode)
		if seen[mode] {
			continue
		}
		seen[mode] = true
		result = append(result, mode)
	}
	sort.Strings(result)
	return result
}

func cloneDeclaration(input map[string]any) map[string]any {
	if input == nil {
		return nil
	}
	return cloneAnyMap(input)
}

func (registry *ToolRegistry) Register(tool RegisteredTool) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	name := strings.TrimSpace(tool.Name)
	if name == "" {
		return fmt.Errorf("tool name is required")
	}
	if tool.Executor == nil {
		return fmt.Errorf("tool %s executor is required", name)
	}
	if tool.Declaration == nil {
		return fmt.Errorf("tool %s declaration is required", name)
	}

	copyTool := RegisteredTool{
		Name:        name,
		Declaration: cloneDeclaration(tool.Declaration),
		Privacy:     normalizeToolPrivacy(tool.Privacy),
		Modes:       cloneModes(tool.Modes),
		Executor:    tool.Executor,
	}

	registry.mu.Lock()
	defer registry.mu.Unlock()
	if registry.tools == nil {
		registry.tools = map[string]RegisteredTool{}
	}
	if _, exists := registry.tools[name]; exists {
		return fmt.Errorf("tool already registered: %s", name)
	}
	registry.tools[name] = copyTool
	return nil
}

func (registry *ToolRegistry) MustRegister(tool RegisteredTool) {
	if err := registry.Register(tool); err != nil {
		panic(err)
	}
}

func (registry *ToolRegistry) Has(name string) bool {
	if registry == nil {
		return false
	}
	registry.mu.RLock()
	defer registry.mu.RUnlock()
	_, ok := registry.tools[strings.TrimSpace(name)]
	return ok
}

func toolAllowsMode(tool RegisteredTool, mode string) bool {
	if len(tool.Modes) == 0 {
		return true
	}
	mode = normalizeToolMode(mode)
	for _, allowed := range tool.Modes {
		if allowed == mode {
			return true
		}
	}
	return false
}

func toolAllowsPrivacy(tool RegisteredTool, allowPrivate bool) bool {
	if allowPrivate {
		return true
	}
	return tool.Privacy == ToolPrivacyPublic
}

func (registry *ToolRegistry) Declarations(mode string, allowPrivate bool) []map[string]any {
	if registry == nil {
		return nil
	}
	registry.mu.RLock()
	defer registry.mu.RUnlock()

	names := make([]string, 0, len(registry.tools))
	for name, tool := range registry.tools {
		if toolAllowsMode(tool, mode) && toolAllowsPrivacy(tool, allowPrivate) {
			names = append(names, name)
		}
	}
	sort.Strings(names)

	declarations := make([]map[string]any, 0, len(names))
	for _, name := range names {
		declarations = append(declarations, cloneDeclaration(registry.tools[name].Declaration))
	}
	return declarations
}

func safeToolError(name string, err error) map[string]any {
	message := "unknown error"
	if err != nil && strings.TrimSpace(err.Error()) != "" {
		message = err.Error()
	}
	return map[string]any{
		"ok":    false,
		"error": fmt.Sprintf("Công cụ %s gặp lỗi nội bộ: %s", name, message),
	}
}

func (registry *ToolRegistry) Execute(
	ctx context.Context,
	toolContext ToolContext,
	name string,
	arguments map[string]any,
	allowPrivate bool,
) any {
	if registry == nil {
		return safeToolError(name, fmt.Errorf("tool registry is not configured"))
	}
	name = strings.TrimSpace(name)
	registry.mu.RLock()
	tool, exists := registry.tools[name]
	registry.mu.RUnlock()
	if !exists {
		return safeToolError(name, fmt.Errorf("unknown tool"))
	}
	if !toolAllowsMode(tool, toolContext.Mode) {
		return safeToolError(name, fmt.Errorf("tool is unavailable in mode %s", normalizeToolMode(toolContext.Mode)))
	}
	if !toolAllowsPrivacy(tool, allowPrivate) {
		return safeToolError(name, fmt.Errorf("tool requires private runtime context"))
	}
	if arguments == nil {
		arguments = map[string]any{}
	} else {
		arguments = cloneAnyMap(arguments)
	}
	value, err := tool.Executor(ctx, toolContext, arguments)
	if err != nil {
		return safeToolError(name, err)
	}
	if value == nil {
		return map[string]any{"ok": true}
	}
	return value
}

func (registry *ToolRegistry) VertexExecutor(
	toolContext ToolContext,
	allowPrivate bool,
) VertexToolExecutor {
	return func(ctx context.Context, name string, arguments map[string]any) (any, error) {
		return registry.Execute(ctx, toolContext, name, arguments, allowPrivate), nil
	}
}
