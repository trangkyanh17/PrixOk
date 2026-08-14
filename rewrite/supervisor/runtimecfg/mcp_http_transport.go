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
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

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
	if client == nil {
		client = &http.Client{}
	}
	return &httpMCPTransport{spec: spec, client: client}
}

func (transport *httpMCPTransport) addHeaders(req *http.Request) {
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
}

func (transport *httpMCPTransport) redactErrorBody(value string) string {
	result := value
	for key, raw := range transport.spec.Headers {
		secret := strings.TrimSpace(raw)
		if secret == "" {
			continue
		}
		foldedKey := strings.ToLower(strings.TrimSpace(key))
		if foldedKey == "authorization" ||
			strings.Contains(foldedKey, "token") ||
			strings.Contains(foldedKey, "secret") ||
			strings.Contains(foldedKey, "key") {
			result = strings.ReplaceAll(result, secret, "[redacted]")
			if strings.HasPrefix(strings.ToLower(secret), "bearer ") {
				token := strings.TrimSpace(secret[len("Bearer "):])
				if token != "" {
					result = strings.ReplaceAll(result, token, "[redacted]")
				}
			}
		}
	}
	return result
}

func decodeSSEMatchingResponse(body io.Reader, expectedID int64) (mcpRPCResponse, error) {
	scanner := bufio.NewScanner(body)
	scanner.Buffer(make([]byte, 64<<10), 4<<20)
	dataLines := []string{}

	flush := func() (mcpRPCResponse, bool) {
		if len(dataLines) == 0 {
			return mcpRPCResponse{}, false
		}
		payload := strings.Join(dataLines, "\n")
		dataLines = dataLines[:0]
		var response mcpRPCResponse
		if json.Unmarshal([]byte(payload), &response) != nil {
			return mcpRPCResponse{}, false
		}
		if response.Method != "" || !rpcIDMatches(response.ID, expectedID) {
			return mcpRPCResponse{}, false
		}
		return response, true
	}

	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			if response, ok := flush(); ok {
				return response, nil
			}
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue
		}
		if strings.HasPrefix(line, "data:") {
			dataLines = append(dataLines, strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
	}
	if response, ok := flush(); ok {
		return response, nil
	}
	if err := scanner.Err(); err != nil {
		return mcpRPCResponse{}, err
	}
	return mcpRPCResponse{}, errors.New("MCP SSE response contained no matching JSON-RPC response")
}

func (transport *httpMCPTransport) postLocked(
	ctx context.Context,
	payload any,
	expectedID *int64,
) (mcpRPCResponse, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return mcpRPCResponse{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, transport.spec.URL, bytes.NewReader(data))
	if err != nil {
		return mcpRPCResponse{}, err
	}
	transport.addHeaders(req)

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
		message := transport.redactErrorBody(strings.TrimSpace(string(body)))
		return mcpRPCResponse{}, fmt.Errorf("MCP HTTP %d: %s", response.StatusCode, message)
	}
	if expectedID == nil {
		return mcpRPCResponse{}, nil
	}
	if response.StatusCode == http.StatusAccepted || response.StatusCode == http.StatusNoContent {
		return mcpRPCResponse{}, fmt.Errorf("MCP HTTP %d returned no response for request id %d", response.StatusCode, *expectedID)
	}

	contentType := strings.ToLower(response.Header.Get("Content-Type"))
	if strings.Contains(contentType, "text/event-stream") {
		return decodeSSEMatchingResponse(response.Body, *expectedID)
	}

	var rpcResponse mcpRPCResponse
	if err := json.NewDecoder(response.Body).Decode(&rpcResponse); err != nil {
		return mcpRPCResponse{}, err
	}
	if rpcResponse.Method != "" || !rpcIDMatches(rpcResponse.ID, *expectedID) {
		return mcpRPCResponse{}, fmt.Errorf("MCP HTTP response id mismatch: expected %d", *expectedID)
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
	}, &id)
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
	response, err := transport.postLocked(ctx, mcpInitializePayload(id), &id)
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
		nil,
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
	return collectMCPTools(func(params map[string]any) (json.RawMessage, error) {
		return transport.requestLocked(ctx, "tools/list", params)
	})
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

func (transport *httpMCPTransport) closeSessionLocked() error {
	if transport.sessionID == "" {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, transport.spec.URL, nil)
	if err != nil {
		return err
	}
	transport.addHeaders(req)
	response, err := transport.client.Do(req)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode >= 200 && response.StatusCode < 300 {
		return nil
	}
	if response.StatusCode == http.StatusMethodNotAllowed ||
		response.StatusCode == http.StatusNotFound ||
		response.StatusCode == http.StatusNotImplemented {
		return nil
	}
	body, _ := io.ReadAll(io.LimitReader(response.Body, 4<<10))
	message := transport.redactErrorBody(strings.TrimSpace(string(body)))
	return fmt.Errorf("MCP HTTP DELETE %d: %s", response.StatusCode, message)
}

func (transport *httpMCPTransport) Close() error {
	transport.mu.Lock()
	defer transport.mu.Unlock()
	err := transport.closeSessionLocked()
	transport.initialized = false
	transport.sessionID = ""
	transport.protocolVersion = ""
	return err
}
