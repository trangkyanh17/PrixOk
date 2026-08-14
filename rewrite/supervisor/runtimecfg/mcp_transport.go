package runtimecfg

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const (
	DefaultMCPRequestTimeout = 180 * time.Second
	DefaultMCPIdleTTL        = time.Hour
	defaultMCPProtocol       = "2025-06-18"
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
			if _, err := backend.stat(configured); err == nil {
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

func (backend *MCPTransportBackend) managedTransport(
	ctx context.Context,
	plugin string,
) (MCPTransport, error) {
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
	transport, err := backend.managedTransport(ctx, plugin)
	if err != nil {
		return nil, err
	}
	callCtx, cancel := context.WithTimeout(ctx, backend.timeout())
	defer cancel()
	tools, err := transport.ListTools(callCtx)
	if err != nil {
		backend.invalidate(plugin, transport)
		return nil, err
	}
	return tools, nil
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
	ID      any             `json:"id"`
	Result  json.RawMessage `json:"result"`
	Error   *mcpRPCError    `json:"error"`
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

func decodeMCPTools(raw json.RawMessage) ([]MCPTool, error) {
	var payload struct {
		Tools []struct {
			Name        string         `json:"name"`
			Description string         `json:"description"`
			InputSchema map[string]any `json:"inputSchema"`
		} `json:"tools"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, err
	}
	tools := make([]MCPTool, 0, len(payload.Tools))
	for _, item := range payload.Tools {
		tools = append(tools, MCPTool{
			Name:        item.Name,
			Description: item.Description,
			InputSchema: item.InputSchema,
		})
	}
	return tools, nil
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

type stdioMCPTransport struct {
	spec MCPPluginSpec

	mu          sync.Mutex
	cmd         *exec.Cmd
	stdin       io.WriteCloser
	stdout      *bufio.Reader
	stderr      bytes.Buffer
	initialized bool
	nextID      int64
}

func newStdioMCPTransport(spec MCPPluginSpec) *stdioMCPTransport {
	return &stdioMCPTransport{spec: spec}
}

func (transport *stdioMCPTransport) startLocked() error {
	if transport.cmd != nil {
		return nil
	}
	cmd := exec.Command(transport.spec.Command, transport.spec.Args...)
	cmd.Env = os.Environ()
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		_ = stdin.Close()
		return err
	}
	cmd.Stderr = &transport.stderr
	if err := cmd.Start(); err != nil {
		_ = stdin.Close()
		return err
	}
	transport.cmd = cmd
	transport.stdin = stdin
	transport.stdout = bufio.NewReader(stdoutPipe)
	return nil
}

func (transport *stdioMCPTransport) abortLocked() {
	if transport.stdin != nil {
		_ = transport.stdin.Close()
	}
	if transport.cmd != nil && transport.cmd.Process != nil {
		_ = transport.cmd.Process.Kill()
		_, _ = transport.cmd.Process.Wait()
	}
	transport.cmd = nil
	transport.stdin = nil
	transport.stdout = nil
	transport.initialized = false
}

func (transport *stdioMCPTransport) writeLocked(payload any) error {
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	data = append(data, '\n')
	_, err = transport.stdin.Write(data)
	return err
}

func (transport *stdioMCPTransport) requestLocked(
	ctx context.Context,
	method string,
	params map[string]any,
) (json.RawMessage, error) {
	id := atomic.AddInt64(&transport.nextID, 1)
	request := map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  method,
		"params":  params,
	}
	if err := transport.writeLocked(request); err != nil {
		return nil, err
	}

	type readResult struct {
		Response mcpRPCResponse
		Err      error
	}
	resultCh := make(chan readResult, 1)
	reader := transport.stdout
	go func() {
		line, err := reader.ReadBytes('\n')
		if err != nil {
			resultCh <- readResult{Err: err}
			return
		}
		var response mcpRPCResponse
		if err := json.Unmarshal(bytes.TrimSpace(line), &response); err != nil {
			resultCh <- readResult{Err: err}
			return
		}
		resultCh <- readResult{Response: response}
	}()

	select {
	case <-ctx.Done():
		transport.abortLocked()
		return nil, ctx.Err()
	case result := <-resultCh:
		if result.Err != nil {
			return nil, result.Err
		}
		if err := rpcError(result.Response); err != nil {
			return nil, err
		}
		return result.Response.Result, nil
	}
}

func (transport *stdioMCPTransport) Initialize(ctx context.Context) error {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	if transport.initialized {
		return nil
	}
	if err := transport.startLocked(); err != nil {
		return err
	}
	id := atomic.AddInt64(&transport.nextID, 1)
	if err := transport.writeLocked(mcpInitializePayload(id)); err != nil {
		transport.abortLocked()
		return err
	}

	type readResult struct {
		Response mcpRPCResponse
		Err      error
	}
	resultCh := make(chan readResult, 1)
	reader := transport.stdout
	go func() {
		line, err := reader.ReadBytes('\n')
		if err != nil {
			resultCh <- readResult{Err: err}
			return
		}
		var response mcpRPCResponse
		if err := json.Unmarshal(bytes.TrimSpace(line), &response); err != nil {
			resultCh <- readResult{Err: err}
			return
		}
		resultCh <- readResult{Response: response}
	}()
	select {
	case <-ctx.Done():
		transport.abortLocked()
		return ctx.Err()
	case result := <-resultCh:
		if result.Err != nil {
			transport.abortLocked()
			return result.Err
		}
		if err := rpcError(result.Response); err != nil {
			transport.abortLocked()
			return err
		}
	}
	if err := transport.writeLocked(mcpNotification("notifications/initialized", map[string]any{})); err != nil {
		transport.abortLocked()
		return err
	}
	transport.initialized = true
	return nil
}

func (transport *stdioMCPTransport) ListTools(ctx context.Context) ([]MCPTool, error) {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	if !transport.initialized {
		return nil, errors.New("MCP stdio transport is not initialized")
	}
	raw, err := transport.requestLocked(ctx, "tools/list", map[string]any{})
	if err != nil {
		return nil, err
	}
	return decodeMCPTools(raw)
}

func (transport *stdioMCPTransport) CallTool(
	ctx context.Context,
	tool string,
	arguments map[string]any,
) (MCPCallResult, error) {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	if !transport.initialized {
		return MCPCallResult{}, errors.New("MCP stdio transport is not initialized")
	}
	raw, err := transport.requestLocked(ctx, "tools/call", map[string]any{
		"name":      tool,
		"arguments": arguments,
	})
	if err != nil {
		return MCPCallResult{}, err
	}
	return decodeMCPCallResult(raw)
}

func (transport *stdioMCPTransport) Close() error {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	if transport.stdin != nil {
		_ = transport.stdin.Close()
	}
	if transport.cmd == nil {
		return nil
	}
	cmd := transport.cmd
	transport.cmd = nil
	transport.stdin = nil
	transport.stdout = nil
	transport.initialized = false
	if cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
	err := cmd.Wait()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return nil
		}
	}
	return err
}

type httpMCPTransport struct {
	spec   MCPPluginSpec
	client *http.Client

	mu              sync.Mutex
	nextID          int64
	sessionID       string
	protocolVersion string
	initialized     bool
}

func newHTTPMCPTransport(spec MCPPluginSpec, client *http.Client) *httpMCPTransport {
	return &httpMCPTransport{spec: spec, client: client}
}

func (transport *httpMCPTransport) postLocked(
	ctx context.Context,
	payload any,
	expectResponse bool,
) (mcpRPCResponse, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return mcpRPCResponse{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, transport.spec.URL, bytes.NewReader(data))
	if err != nil {
		return mcpRPCResponse{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	for key, value := range transport.spec.Headers {
		req.Header.Set(key, value)
	}
	if transport.sessionID != "" {
		req.Header.Set("Mcp-Session-Id", transport.sessionID)
	}
	if transport.protocolVersion != "" {
		req.Header.Set("MCP-Protocol-Version", transport.protocolVersion)
	}

	response, err := transport.client.Do(req)
	if err != nil {
		return mcpRPCResponse{}, err
	}
	defer response.Body.Close()
	if sessionID := strings.TrimSpace(response.Header.Get("Mcp-Session-Id")); sessionID != "" {
		transport.sessionID = sessionID
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 16<<10))
		return mcpRPCResponse{}, fmt.Errorf("MCP HTTP %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}
	if !expectResponse || response.StatusCode == http.StatusAccepted || response.StatusCode == http.StatusNoContent {
		return mcpRPCResponse{}, nil
	}

	contentType := strings.ToLower(response.Header.Get("Content-Type"))
	if strings.Contains(contentType, "text/event-stream") {
		scanner := bufio.NewScanner(response.Body)
		scanner.Buffer(make([]byte, 64<<10), 4<<20)
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if !strings.HasPrefix(line, "data:") {
				continue
			}
			data := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
			if data == "" {
				continue
			}
			var rpcResponse mcpRPCResponse
			if err := json.Unmarshal([]byte(data), &rpcResponse); err != nil {
				continue
			}
			return rpcResponse, nil
		}
		if err := scanner.Err(); err != nil {
			return mcpRPCResponse{}, err
		}
		return mcpRPCResponse{}, errors.New("MCP SSE response contained no JSON-RPC data")
	}

	var rpcResponse mcpRPCResponse
	if err := json.NewDecoder(response.Body).Decode(&rpcResponse); err != nil {
		return mcpRPCResponse{}, err
	}
	return rpcResponse, nil
}

func (transport *httpMCPTransport) requestLocked(
	ctx context.Context,
	method string,
	params map[string]any,
) (json.RawMessage, error) {
	id := atomic.AddInt64(&transport.nextID, 1)
	response, err := transport.postLocked(ctx, map[string]any{
		"jsonrpc": "2.0",
		"id":      id,
		"method":  method,
		"params":  params,
	}, true)
	if err != nil {
		return nil, err
	}
	if err := rpcError(response); err != nil {
		return nil, err
	}
	return response.Result, nil
}

func (transport *httpMCPTransport) Initialize(ctx context.Context) error {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	if transport.initialized {
		return nil
	}
	id := atomic.AddInt64(&transport.nextID, 1)
	response, err := transport.postLocked(ctx, mcpInitializePayload(id), true)
	if err != nil {
		return err
	}
	if err := rpcError(response); err != nil {
		return err
	}
	var initialized struct {
		ProtocolVersion string `json:"protocolVersion"`
	}
	if len(response.Result) > 0 {
		_ = json.Unmarshal(response.Result, &initialized)
	}
	transport.protocolVersion = strings.TrimSpace(initialized.ProtocolVersion)
	if transport.protocolVersion == "" {
		transport.protocolVersion = defaultMCPProtocol
	}
	if _, err := transport.postLocked(
		ctx,
		mcpNotification("notifications/initialized", map[string]any{}),
		false,
	); err != nil {
		return err
	}
	transport.initialized = true
	return nil
}

func (transport *httpMCPTransport) ListTools(ctx context.Context) ([]MCPTool, error) {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	if !transport.initialized {
		return nil, errors.New("MCP HTTP transport is not initialized")
	}
	raw, err := transport.requestLocked(ctx, "tools/list", map[string]any{})
	if err != nil {
		return nil, err
	}
	return decodeMCPTools(raw)
}

func (transport *httpMCPTransport) CallTool(
	ctx context.Context,
	tool string,
	arguments map[string]any,
) (MCPCallResult, error) {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	if !transport.initialized {
		return MCPCallResult{}, errors.New("MCP HTTP transport is not initialized")
	}
	raw, err := transport.requestLocked(ctx, "tools/call", map[string]any{
		"name":      tool,
		"arguments": arguments,
	})
	if err != nil {
		return MCPCallResult{}, err
	}
	return decodeMCPCallResult(raw)
}

func (transport *httpMCPTransport) Close() error {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	transport.initialized = false
	transport.sessionID = ""
	transport.protocolVersion = ""
	return nil
}
