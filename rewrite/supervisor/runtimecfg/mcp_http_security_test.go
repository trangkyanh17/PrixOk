package runtimecfg

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHTTPMCPTransportRedactsSecretsFromServerErrors(t *testing.T) {
	const token = "ghp-test-super-secret"
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusUnauthorized)
		_, _ = writer.Write([]byte("authorization=Bearer " + token + " token=" + token))
	}))
	defer server.Close()

	transport := newHTTPMCPTransport(MCPPluginSpec{
		Name:      "github",
		Transport: "http",
		URL:       server.URL,
		Headers: map[string]string{
			"Authorization": "Bearer " + token,
		},
	}, server.Client())

	err := transport.Initialize(context.Background())
	if err == nil {
		t.Fatal("expected HTTP initialization error")
	}
	message := err.Error()
	if strings.Contains(message, token) || strings.Contains(message, "Bearer "+token) {
		t.Fatalf("secret leaked in error: %q", message)
	}
	if !strings.Contains(message, "[redacted]") {
		t.Fatalf("redaction marker missing: %q", message)
	}
}

func TestHTTPMCPTransportRedactsAPIKeyHeaderValues(t *testing.T) {
	transport := newHTTPMCPTransport(MCPPluginSpec{
		Headers: map[string]string{"CONTEXT7_API_KEY": "ctx-secret"},
	}, nil)
	message := transport.redactErrorBody("failure key=ctx-secret")
	if strings.Contains(message, "ctx-secret") || !strings.Contains(message, "[redacted]") {
		t.Fatalf("message=%q", message)
	}
}
