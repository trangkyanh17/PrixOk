package runtimecfg

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type scriptedHTTPDoer struct {
	responses []*http.Response
	errors    []error
	calls     int
}

func (doer *scriptedHTTPDoer) Do(request *http.Request) (*http.Response, error) {
	index := doer.calls
	doer.calls++
	if index < len(doer.errors) && doer.errors[index] != nil {
		return nil, doer.errors[index]
	}
	if index >= len(doer.responses) || doer.responses[index] == nil {
		return nil, errors.New("missing scripted response")
	}
	return doer.responses[index], nil
}

func vertexResponse(status int, body string, headers map[string]string) *http.Response {
	header := http.Header{}
	for key, value := range headers {
		header.Set(key, value)
	}
	return &http.Response{
		StatusCode: status,
		Header:     header,
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func TestPostVertexSuccessAndCredentialRefreshSequence(t *testing.T) {
	doer := &scriptedHTTPDoer{
		responses: []*http.Response{
			vertexResponse(401, `{"error":{"message":"expired"}}`, nil),
			vertexResponse(200, `{"ok":true}`, nil),
		},
	}
	forceRefresh := []bool{}
	tokens := []string{}
	provider := func(ctx context.Context, force bool) (string, error) {
		forceRefresh = append(forceRefresh, force)
		token := "token-a"
		if force {
			token = "token-b"
		}
		tokens = append(tokens, token)
		return token, nil
	}
	sleeps := []time.Duration{}
	result, err := PostVertex(
		context.Background(),
		doer,
		"https://example.test",
		map[string]any{"hello": "world"},
		provider,
		func(ctx context.Context, duration time.Duration) error {
			sleeps = append(sleeps, duration)
			return nil
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if result["ok"] != true {
		t.Fatalf("result=%v", result)
	}
	if len(forceRefresh) != 2 || forceRefresh[0] || !forceRefresh[1] {
		t.Fatalf("force refresh=%v", forceRefresh)
	}
	if len(tokens) != 2 || tokens[0] != "token-a" || tokens[1] != "token-b" {
		t.Fatalf("tokens=%v", tokens)
	}
	if len(sleeps) != 0 {
		t.Fatalf("401 first retry must not sleep: %v", sleeps)
	}
}

func TestPostVertexRetriesTransientStatusWithBackoff(t *testing.T) {
	doer := &scriptedHTTPDoer{
		responses: []*http.Response{
			vertexResponse(429, `{}`, nil),
			vertexResponse(503, `{}`, nil),
			vertexResponse(200, `{"done":1}`, nil),
		},
	}
	forceRefresh := []bool{}
	sleeps := []time.Duration{}
	result, err := PostVertex(
		context.Background(),
		doer,
		"https://example.test",
		map[string]any{},
		func(ctx context.Context, force bool) (string, error) {
			forceRefresh = append(forceRefresh, force)
			return "token", nil
		},
		func(ctx context.Context, duration time.Duration) error {
			sleeps = append(sleeps, duration)
			return nil
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if result["done"] != float64(1) {
		t.Fatalf("result=%v", result)
	}
	if len(sleeps) != 2 || sleeps[0] != 1500*time.Millisecond || sleeps[1] != 3*time.Second {
		t.Fatalf("sleeps=%v", sleeps)
	}
	if len(forceRefresh) != 3 || forceRefresh[0] || !forceRefresh[1] || forceRefresh[2] {
		t.Fatalf("force refresh=%v", forceRefresh)
	}
}

func TestPostVertexRetriesNetworkErrorsAndReturnsNetworkReason(t *testing.T) {
	doer := &scriptedHTTPDoer{
		errors: []error{
			errors.New("network one"),
			errors.New("network two"),
			errors.New("network three"),
		},
	}
	sleeps := []time.Duration{}
	_, err := PostVertex(
		context.Background(),
		doer,
		"https://example.test",
		map[string]any{},
		func(context.Context, bool) (string, error) { return "token", nil },
		func(ctx context.Context, duration time.Duration) error {
			sleeps = append(sleeps, duration)
			return nil
		},
	)
	vertexErr, ok := err.(*VertexHTTPError)
	if !ok || vertexErr.Reason != "NETWORK_ERROR" || vertexErr.StatusCode != 0 {
		t.Fatalf("err=%T %+v", err, err)
	}
	if len(sleeps) != 2 || sleeps[0] != 1500*time.Millisecond || sleeps[1] != 3*time.Second {
		t.Fatalf("sleeps=%v", sleeps)
	}
}

func TestPostVertexParsesHTTPErrorReasonAndRequestID(t *testing.T) {
	doer := &scriptedHTTPDoer{
		responses: []*http.Response{
			vertexResponse(403, `{"error":{"message":"forbidden","status":"PERMISSION_DENIED","code":403}}`, map[string]string{
				"traceparent":       "trace-id",
				"x-request-id":      "request-id",
				"x-goog-request-id": "google-id",
			}),
		},
	}
	_, err := PostVertex(
		context.Background(),
		doer,
		"https://example.test",
		map[string]any{},
		func(context.Context, bool) (string, error) { return "token", nil },
		nil,
	)
	vertexErr, ok := err.(*VertexHTTPError)
	if !ok {
		t.Fatalf("err=%T %v", err, err)
	}
	if vertexErr.StatusCode != 403 || vertexErr.Reason != "PERMISSION_DENIED" || vertexErr.RequestID != "google-id" {
		t.Fatalf("err=%+v", vertexErr)
	}
	if !strings.Contains(vertexErr.Message, "forbidden") || !strings.Contains(vertexErr.Message, "request_id=google-id") {
		t.Fatalf("message=%q", vertexErr.Message)
	}
}

func TestPostVertexUsesNumericErrorCodeWhenStatusMissing(t *testing.T) {
	doer := &scriptedHTTPDoer{
		responses: []*http.Response{
			vertexResponse(400, `{"error":{"message":"bad","code":400}}`, nil),
		},
	}
	_, err := PostVertex(
		context.Background(),
		doer,
		"https://example.test",
		map[string]any{},
		func(context.Context, bool) (string, error) { return "token", nil },
		nil,
	)
	vertexErr := err.(*VertexHTTPError)
	if vertexErr.Reason != "400" {
		t.Fatalf("reason=%q", vertexErr.Reason)
	}
}

func TestPostVertexRejectsInvalidSuccessJSON(t *testing.T) {
	doer := &scriptedHTTPDoer{
		responses: []*http.Response{vertexResponse(200, "not-json", nil)},
	}
	_, err := PostVertex(
		context.Background(),
		doer,
		"https://example.test",
		map[string]any{},
		func(context.Context, bool) (string, error) { return "token", nil },
		nil,
	)
	if err == nil || err.Error() != "Vertex trả về JSON không hợp lệ" {
		t.Fatalf("err=%v", err)
	}
}

func TestPostVertexCredentialFailureIsNotRetried(t *testing.T) {
	calls := 0
	_, err := PostVertex(
		context.Background(),
		&scriptedHTTPDoer{},
		"https://example.test",
		map[string]any{},
		func(context.Context, bool) (string, error) {
			calls++
			return "", errors.New("credential failed")
		},
		nil,
	)
	if err == nil || err.Error() != "credential failed" || calls != 1 {
		t.Fatalf("err=%v calls=%d", err, calls)
	}
}
