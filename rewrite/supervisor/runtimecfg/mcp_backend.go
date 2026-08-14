package runtimecfg

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

const (
	DefaultMCPRequestTimeout = 180 * time.Second
	DefaultMCPIdleTTL        = time.Hour
	defaultMCPProtocol       = "2025-06-18"
	maxMCPToolPages          = 64
)

type MCPPluginSpec struct {
	Name        string
	Transport   string
	Command     string
	Args        []string
	URL         string
	Headers     map[string]string
	Requirement string
	Description string
}

type MCPTransport interface {
	Initialize(context.Context) error
	ListTools(context.Context) ([]MCPTool, error)
	CallTool(context.Context, string, map[string]any) (MCPCallResult, error)
	Close() error
}

type MCPTransportFactory func(MCPPluginSpec) (MCPTransport, error)

type mcpManagedTransport struct {
	Transport MCPTransport
	LastUsed  time.Time
}

type MCPTransportBackend struct {
	Values         map[string]string
	RequestTimeout time.Duration
	IdleTTL        time.Duration
	HTTPClient     *http.Client
	Factory        MCPTransportFactory
	LookPath       func(string) (string, error)
	Stat           func(string) (os.FileInfo, error)
	Now            func() time.Time

	mu       sync.Mutex
	sessions map[string]mcpManagedTransport
	locks    map[string]*sync.Mutex
}

func NewMCPTransportBackend(values map[string]string) *MCPTransportBackend {
	return &MCPTransportBackend{
		Values:         cloneStringMap(values),
		RequestTimeout: DefaultMCPRequestTimeout,
		IdleTTL:        DefaultMCPIdleTTL,
	}
}

func (backend *MCPTransportBackend) now() time.Time {
	if backend != nil && backend.Now != nil {
		return backend.Now()
	}
	return time.Now()
}

func (backend *MCPTransportBackend) timeout() time.Duration {
	if backend == nil || backend.RequestTimeout <= 0 {
		return DefaultMCPRequestTimeout
	}
	return backend.RequestTimeout
}

func (backend *MCPTransportBackend) idleTTL() time.Duration {
	if backend == nil || backend.IdleTTL <= 0 {
		return DefaultMCPIdleTTL
	}
	return backend.IdleTTL
}

func (backend *MCPTransportBackend) setting(names ...string) string {
	for _, name := range names {
		if backend != nil && backend.Values != nil {
			if value := strings.TrimSpace(backend.Values[name]); value != "" {
				return value
			}
		}
		if value := strings.TrimSpace(os.Getenv(name)); value != "" {
			return value
		}
	}
	return ""
}

func (backend *MCPTransportBackend) lookPath(command string) (string, error) {
	if backend != nil && backend.LookPath != nil {
		return backend.LookPath(command)
	}
	return exec.LookPath(command)
}

func (backend *MCPTransportBackend) stat(path string) (os.FileInfo, error) {
	if backend != nil && backend.Stat != nil {
		return backend.Stat(path)
	}
	return os.Stat(path)
}

func (backend *MCPTransportBackend) browserPath() string {
	if configured := backend.setting("ATRI_BROWSER_EXECUTABLE"); configured != "" {
		if strings.HasPrefix(configured, "/") {
			if info, err := backend.stat(configured); err == nil && !info.IsDir() {
				return configured
			}
		} else if path, err := backend.lookPath(configured); err == nil {
			return path
		}
	}
	for _, command := range []string{
		"google-chrome-stable",
		"google-chrome",
		"chromium",
		"chromium-browser",
	} {
		if path, err := backend.lookPath(command); err == nil {
			return path
		}
	}
	return ""
}

func (backend *MCPTransportBackend) PluginSpecs() map[string]MCPPluginSpec {
	uvx := backend.setting("ATRI_UVX")
	if uvx == "" {
		uvx = "/app/mltbenv/bin/uvx"
	}
	projectRoot := backend.setting("ATRI_CODE_PROJECT_ROOT")
	if projectRoot == "" {
		projectRoot = "/app"
	}

	context7Headers := map[string]string{}
	if key := backend.setting("CONTEXT7_API_KEY"); key != "" {
		context7Headers["CONTEXT7_API_KEY"] = key
	}

	githubHeaders := map[string]string{
		"X-MCP-Readonly": "true",
		"X-MCP-Toolsets": "repos,issues,pull_requests,actions,code_security",
	}
	if token := backend.setting("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN"); token != "" {
		githubHeaders["Authorization"] = "Bearer " + token
	}

	browser := backend.browserPath()
	if browser == "" {
		browser = "/usr/bin/google-chrome"
	}

	return map[string]MCPPluginSpec{
		"serena": {
			Name:      "serena",
			Transport: "stdio",
			Command:   uvx,
			Args: []string{
				"--from",
				"git+https://github.com/oraios/serena",
				"serena",
				"start-mcp-server",
				"--context",
				"agent",
				"--project",
				projectRoot,
			},
			Description: "Semantic source/codebase understanding.",
		},
		"context7": {
			Name:        "context7",
			Transport:   "http",
			URL:         "https://mcp.context7.com/mcp",
			Headers:     context7Headers,
			Description: "Current library/API documentation.",
		},
		"github": {
			Name:        "github",
			Transport:   "http",
			URL:         "https://api.githubcopilot.com/mcp/",
			Headers:     githubHeaders,
			Requirement: "github_token",
			Description: "GitHub repositories, issues, PRs and Actions.",
		},
		"semgrep": {
			Name:        "semgrep",
			Transport:   "stdio",
			Command:     uvx,
			Args:        []string{"-p", "3.13", "semgrep", "mcp"},
			Description: "Static/security analysis with Semgrep.",
		},
		"sentry": {
			Name:        "sentry",
			Transport:   "stdio",
			Command:     "npx",
			Args:        []string{"-y", "@sentry/mcp-server@latest"},
			Requirement: "sentry_token",
			Description: "Production errors, events and traces.",
		},
		"chrome-devtools": {
			Name:        "chrome-devtools",
			Transport:   "stdio",
			Command:     "npx",
			Requirement: "browser",
			Args: []string{
				"-y",
				"chrome-devtools-mcp@latest",
				"--headless",
				"--isolated",
				"--executable-path=" + browser,
				"--redact-network-headers",
				"--chrome-arg=--no-sandbox",
				"--chrome-arg=--disable-dev-shm-usage",
			},
			Description: "Browser DOM/network/console/performance debugging.",
		},
	}
}

func (backend *MCPTransportBackend) pluginSpec(plugin string) (MCPPluginSpec, bool) {
	plugin = normalizeMCPPlugin(plugin)
	spec, ok := backend.PluginSpecs()[plugin]
	return spec, ok
}

func (backend *MCPTransportBackend) commandAvailable(command string) bool {
	command = strings.TrimSpace(command)
	if command == "" {
		return false
	}
	if strings.HasPrefix(command, "/") {
		info, err := backend.stat(command)
		return err == nil && !info.IsDir()
	}
	_, err := backend.lookPath(command)
	return err == nil
}

func (backend *MCPTransportBackend) PluginStatus(_ context.Context, plugin string) (bool, string) {
	spec, ok := backend.pluginSpec(plugin)
	if !ok {
		return false, "unknown plugin"
	}

	switch spec.Requirement {
	case "github_token":
		if backend.setting("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN") == "" {
			return false, "missing GITHUB_PERSONAL_ACCESS_TOKEN/GITHUB_TOKEN"
		}
	case "sentry_token":
		if backend.setting("SENTRY_ACCESS_TOKEN") == "" {
			return false, "missing SENTRY_ACCESS_TOKEN"
		}
	case "browser":
		if backend.browserPath() == "" {
			return false, "Chrome/Chromium is not installed"
		}
	}

	if spec.Transport == "stdio" && !backend.commandAvailable(spec.Command) {
		return false, "command not found: " + spec.Command
	}
	if spec.Transport == "http" && strings.TrimSpace(spec.URL) == "" {
		return false, "MCP endpoint is not configured"
	}
	return true, "ready"
}

func (backend *MCPTransportBackend) pluginLock(plugin string) *sync.Mutex {
	backend.mu.Lock()
	defer backend.mu.Unlock()
	if backend.locks == nil {
		backend.locks = map[string]*sync.Mutex{}
	}
	if backend.locks[plugin] == nil {
		backend.locks[plugin] = &sync.Mutex{}
	}
	return backend.locks[plugin]
}

func (backend *MCPTransportBackend) transportFactory(spec MCPPluginSpec) (MCPTransport, error) {
	if backend.Factory != nil {
		return backend.Factory(spec)
	}
	switch spec.Transport {
	case "stdio":
		return newStdioMCPTransport(spec), nil
	case "http":
		client := backend.HTTPClient
		if client == nil {
			client = &http.Client{}
		}
		return newHTTPMCPTransport(spec, client), nil
	default:
		return nil, fmt.Errorf("unsupported MCP transport: %s", spec.Transport)
	}
}

func (backend *MCPTransportBackend) managedTransport(ctx context.Context, plugin string) (MCPTransport, error) {
	if backend == nil {
		return nil, errors.New("MCP transport backend is nil")
	}
	plugin = normalizeMCPPlugin(plugin)
	ready, reason := backend.PluginStatus(ctx, plugin)
	if !ready {
		return nil, errors.New(reason)
	}

	lock := backend.pluginLock(plugin)
	lock.Lock()
	defer lock.Unlock()

	now := backend.now()
	backend.mu.Lock()
	if backend.sessions == nil {
		backend.sessions = map[string]mcpManagedTransport{}
	}
	managed, exists := backend.sessions[plugin]
	if exists && now.Sub(managed.LastUsed) < backend.idleTTL() {
		managed.LastUsed = now
		backend.sessions[plugin] = managed
		backend.mu.Unlock()
		return managed.Transport, nil
	}
	if exists {
		delete(backend.sessions, plugin)
	}
	backend.mu.Unlock()
	if exists {
		_ = managed.Transport.Close()
	}

	spec, _ := backend.pluginSpec(plugin)
	transport, err := backend.transportFactory(spec)
	if err != nil {
		return nil, err
	}
	initCtx, cancel := context.WithTimeout(ctx, backend.timeout())
	defer cancel()
	if err := transport.Initialize(initCtx); err != nil {
		_ = transport.Close()
		return nil, err
	}

	backend.mu.Lock()
	backend.sessions[plugin] = mcpManagedTransport{Transport: transport, LastUsed: now}
	backend.mu.Unlock()
	return transport, nil
}

func (backend *MCPTransportBackend) invalidate(plugin string, transport MCPTransport) {
	if backend == nil {
		return
	}
	plugin = normalizeMCPPlugin(plugin)
	backend.mu.Lock()
	managed, ok := backend.sessions[plugin]
	if ok && managed.Transport == transport {
		delete(backend.sessions, plugin)
	}
	backend.mu.Unlock()
	if ok && managed.Transport == transport {
		_ = transport.Close()
	}
}

func (backend *MCPTransportBackend) ListTools(ctx context.Context, plugin string) ([]MCPTool, error) {
	for attempt := 0; attempt < 2; attempt++ {
		transport, err := backend.managedTransport(ctx, plugin)
		if err != nil {
			return nil, err
		}
		callCtx, cancel := context.WithTimeout(ctx, backend.timeout())
		tools, err := transport.ListTools(callCtx)
		cancel()
		if err == nil {
			return tools, nil
		}
		backend.invalidate(plugin, transport)
		if attempt == 1 {
			return nil, err
		}
	}
	return nil, errors.New("MCP discovery retry exhausted")
}

func (backend *MCPTransportBackend) CallTool(
	ctx context.Context,
	plugin string,
	tool string,
	arguments map[string]any,
) (MCPCallResult, error) {
	transport, err := backend.managedTransport(ctx, plugin)
	if err != nil {
		return MCPCallResult{}, err
	}
	callCtx, cancel := context.WithTimeout(ctx, backend.timeout())
	defer cancel()
	result, err := transport.CallTool(callCtx, tool, cloneAnyMap(arguments))
	if err != nil {
		backend.invalidate(plugin, transport)
		return MCPCallResult{}, err
	}
	return result, nil
}

func (backend *MCPTransportBackend) PruneIdle() int {
	if backend == nil {
		return 0
	}
	now := backend.now()
	backend.mu.Lock()
	stale := make([]MCPTransport, 0)
	for plugin, managed := range backend.sessions {
		if now.Sub(managed.LastUsed) >= backend.idleTTL() {
			stale = append(stale, managed.Transport)
			delete(backend.sessions, plugin)
		}
	}
	backend.mu.Unlock()
	for _, transport := range stale {
		_ = transport.Close()
	}
	return len(stale)
}

func (backend *MCPTransportBackend) Prewarm(
	ctx context.Context,
	plugins []string,
	concurrency int,
) map[string]string {
	if len(plugins) == 0 {
		plugins = append([]string(nil), MCPPluginNames...)
	}
	if concurrency <= 0 {
		concurrency = 2
	}
	if concurrency > len(plugins) && len(plugins) > 0 {
		concurrency = len(plugins)
	}

	results := map[string]string{}
	var mu sync.Mutex
	sem := make(chan struct{}, concurrency)
	var wg sync.WaitGroup
	for _, raw := range plugins {
		plugin := normalizeMCPPlugin(raw)
		if plugin == "" {
			continue
		}
		wg.Add(1)
		go func() {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-ctx.Done():
				mu.Lock()
				results[plugin] = ctx.Err().Error()
				mu.Unlock()
				return
			}
			tools, err := backend.ListTools(ctx, plugin)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				results[plugin] = err.Error()
				return
			}
			results[plugin] = fmt.Sprintf("ready:%d", len(tools))
		}()
	}
	wg.Wait()
	return results
}

func (backend *MCPTransportBackend) Close() error {
	if backend == nil {
		return nil
	}
	backend.mu.Lock()
	sessions := backend.sessions
	backend.sessions = map[string]mcpManagedTransport{}
	backend.mu.Unlock()
	var joined error
	for _, managed := range sessions {
		joined = errors.Join(joined, managed.Transport.Close())
	}
	return joined
}

type mcpRPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

type mcpRPCResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Method  string          `json:"method,omitempty"`
	Params  json.RawMessage `json:"params,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *mcpRPCError    `json:"error,omitempty"`
}

func rpcError(response mcpRPCResponse) error {
	if response.Error == nil {
		return nil
	}
	if response.Error.Data != nil {
		return fmt.Errorf("MCP RPC %d: %s (%v)", response.Error.Code, response.Error.Message, response.Error.Data)
	}
	return fmt.Errorf("MCP RPC %d: %s", response.Error.Code, response.Error.Message)
}

func rpcIDMatches(raw json.RawMessage, id int64) bool {
	if len(raw) == 0 {
		return false
	}
	var number int64
	return json.Unmarshal(raw, &number) == nil && number == id
}

func mcpInitializePayload(id int64) map[string]any {
	return map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  "initialize",
		"params": map[string]any{
			"protocolVersion": defaultMCPProtocol,
			"capabilities":    map[string]any{},
			"clientInfo": map[string]any{
				"name":    "atri-rewrite",
				"version": "v150",
			},
		},
	}
}

func mcpNotification(method string, params map[string]any) map[string]any {
	return map[string]any{
		"jsonrpc": "2.0",
		"method":  method,
		"params":  params,
	}
}

func mcpUnsupportedRequest(id json.RawMessage, method string) map[string]any {
	var decoded any
	if err := json.Unmarshal(id, &decoded); err != nil {
		decoded = nil
	}
	return map[string]any{
		"jsonrpc": "2.0",
		"id":      decoded,
		"error": map[string]any{
			"code":    -32601,
			"message": "Client method not supported: " + method,
		},
	}
}

type mcpToolPage struct {
	Tools      []MCPTool
	NextCursor string
}

func decodeMCPToolPage(raw json.RawMessage) (mcpToolPage, error) {
	var payload struct {
		Tools []struct {
			Name        string         `json:"name"`
			Description string         `json:"description"`
			InputSchema map[string]any `json:"inputSchema"`
		} `json:"tools"`
		NextCursor string `json:"nextCursor"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return mcpToolPage{}, err
	}
	tools := make([]MCPTool, 0, len(payload.Tools))
	for _, item := range payload.Tools {
		tools = append(tools, MCPTool{
			Name:        item.Name,
			Description: item.Description,
			InputSchema: item.InputSchema,
		})
	}
	return mcpToolPage{Tools: tools, NextCursor: strings.TrimSpace(payload.NextCursor)}, nil
}

func decodeMCPTools(raw json.RawMessage) ([]MCPTool, error) {
	page, err := decodeMCPToolPage(raw)
	return page.Tools, err
}

func collectMCPTools(
	request func(map[string]any) (json.RawMessage, error),
) ([]MCPTool, error) {
	params := map[string]any{}
	seenCursors := map[string]struct{}{}
	tools := []MCPTool{}
	for pageIndex := 0; pageIndex < maxMCPToolPages; pageIndex++ {
		raw, err := request(params)
		if err != nil {
			return nil, err
		}
		page, err := decodeMCPToolPage(raw)
		if err != nil {
			return nil, err
		}
		tools = append(tools, page.Tools...)
		if page.NextCursor == "" {
			return tools, nil
		}
		if _, seen := seenCursors[page.NextCursor]; seen {
			return nil, fmt.Errorf("MCP tools/list repeated cursor %q", page.NextCursor)
		}
		seenCursors[page.NextCursor] = struct{}{}
		params = map[string]any{"cursor": page.NextCursor}
	}
	return nil, fmt.Errorf("MCP tools/list exceeded %d pages", maxMCPToolPages)
}

func decodeMCPCallResult(raw json.RawMessage) (MCPCallResult, error) {
	var payload struct {
		Content           []any `json:"content"`
		StructuredContent any   `json:"structuredContent"`
		IsError           bool  `json:"isError"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return MCPCallResult{}, err
	}
	return MCPCallResult{
		Content:    payload.Content,
		Structured: payload.StructuredContent,
		IsError:    payload.IsError,
	}, nil
}
