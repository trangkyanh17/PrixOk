package runtimecfg

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"sync"
	"sync/atomic"
	"syscall"
)

const defaultMCPStderrLimit = 64 << 10

type boundedByteBuffer struct {
	mu  sync.Mutex
	buf []byte
	max int
}

func newBoundedByteBuffer(max int) *boundedByteBuffer {
	if max <= 0 {
		max = defaultMCPStderrLimit
	}
	return &boundedByteBuffer{max: max}
}

func (buffer *boundedByteBuffer) Write(data []byte) (int, error) {
	buffer.mu.Lock()
	defer buffer.mu.Unlock()
	original := len(data)
	if len(data) >= buffer.max {
		buffer.buf = append(buffer.buf[:0], data[len(data)-buffer.max:]...)
		return original, nil
	}
	needed := len(buffer.buf) + len(data) - buffer.max
	if needed > 0 {
		copy(buffer.buf, buffer.buf[needed:])
		buffer.buf = buffer.buf[:len(buffer.buf)-needed]
	}
	buffer.buf = append(buffer.buf, data...)
	return original, nil
}

func (buffer *boundedByteBuffer) Reset() {
	buffer.mu.Lock()
	buffer.buf = buffer.buf[:0]
	buffer.mu.Unlock()
}

type stdioMCPTransport struct {
	spec MCPPluginSpec

	mu          sync.Mutex
	cmd         *exec.Cmd
	stdin       io.WriteCloser
	stdout      *bufio.Reader
	stderr      *boundedByteBuffer
	initialized bool
	nextID      int64
}

func newStdioMCPTransport(spec MCPPluginSpec) *stdioMCPTransport {
	return &stdioMCPTransport{
		spec:   spec,
		stderr: newBoundedByteBuffer(defaultMCPStderrLimit),
	}
}

func (transport *stdioMCPTransport) startLocked() error {
	if transport.cmd != nil {
		return nil
	}
	cmd := exec.Command(transport.spec.Command, transport.spec.Args...)
	cmd.Env = os.Environ()
	// uvx/npx frequently spawn a child runtime. Put every MCP launch in its
	// own process group so timeout/close cannot leave descendants orphaned.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		_ = stdin.Close()
		return err
	}
	transport.stderr.Reset()
	cmd.Stderr = transport.stderr
	if err := cmd.Start(); err != nil {
		_ = stdin.Close()
		return err
	}
	transport.cmd = cmd
	transport.stdin = stdin
	transport.stdout = bufio.NewReader(stdoutPipe)
	return nil
}

func stopMCPCommand(cmd *exec.Cmd) error {
	if cmd == nil {
		return nil
	}
	if cmd.Process != nil {
		if pgid, err := syscall.Getpgid(cmd.Process.Pid); err == nil && pgid > 0 {
			_ = syscall.Kill(-pgid, syscall.SIGKILL)
		} else {
			_ = cmd.Process.Kill()
		}
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

func (transport *stdioMCPTransport) abortLocked() {
	if transport.stdin != nil {
		_ = transport.stdin.Close()
	}
	cmd := transport.cmd
	transport.cmd = nil
	transport.stdin = nil
	transport.stdout = nil
	transport.initialized = false
	_ = stopMCPCommand(cmd)
}

func writeMCPPayload(writer io.Writer, payload any) error {
	if writer == nil {
		return errors.New("MCP stdio stdin is closed")
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	data = append(data, '\n')
	_, err = writer.Write(data)
	return err
}

func (transport *stdioMCPTransport) writeLocked(payload any) error {
	return writeMCPPayload(transport.stdin, payload)
}

func (transport *stdioMCPTransport) readMatchingResponseLocked(
	ctx context.Context,
	id int64,
) (mcpRPCResponse, error) {
	type readResult struct {
		Response mcpRPCResponse
		Err      error
	}
	resultCh := make(chan readResult, 1)
	reader := transport.stdout
	writer := transport.stdin
	go func() {
		for {
			line, err := reader.ReadBytes('\n')
			if err != nil {
				resultCh <- readResult{Err: err}
				return
			}
			line = bytes.TrimSpace(line)
			if len(line) == 0 {
				continue
			}
			var response mcpRPCResponse
			if err := json.Unmarshal(line, &response); err != nil {
				resultCh <- readResult{Err: err}
				return
			}
			if response.Method != "" {
				if len(response.ID) > 0 && string(response.ID) != "null" {
					if err := writeMCPPayload(writer, mcpUnsupportedRequest(response.ID, response.Method)); err != nil {
						resultCh <- readResult{Err: err}
						return
					}
				}
				continue
			}
			if !rpcIDMatches(response.ID, id) {
				continue
			}
			resultCh <- readResult{Response: response}
			return
		}
	}()

	select {
	case <-ctx.Done():
		transport.abortLocked()
		return mcpRPCResponse{}, ctx.Err()
	case result := <-resultCh:
		return result.Response, result.Err
	}
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
	response, err := transport.readMatchingResponseLocked(ctx, id)
	if err != nil {
		return nil, err
	}
	if err := rpcError(response); err != nil {
		return nil, err
	}
	return response.Result, nil
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
	response, err := transport.readMatchingResponseLocked(ctx, id)
	if err != nil {
		transport.abortLocked()
		return err
	}
	if err := rpcError(response); err != nil {
		transport.abortLocked()
		return err
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
	return collectMCPTools(func(params map[string]any) (json.RawMessage, error) {
		return transport.requestLocked(ctx, "tools/list", params)
	})
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
	cmd := transport.cmd
	transport.cmd = nil
	transport.stdin = nil
	transport.stdout = nil
	transport.initialized = false
	return stopMCPCommand(cmd)
}
