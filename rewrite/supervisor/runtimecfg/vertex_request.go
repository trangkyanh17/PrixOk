package runtimecfg

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type VertexTokenProvider func(ctx context.Context, forceRefresh bool) (string, error)
type VertexSleepFunc func(ctx context.Context, duration time.Duration) error

type VertexHTTPError struct {
	Message    string
	StatusCode int
	Reason     string
	RequestID  string
}

func (err *VertexHTTPError) Error() string {
	return err.Message
}

func defaultVertexSleep(ctx context.Context, duration time.Duration) error {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func vertexRequestID(headers http.Header) string {
	for _, name := range []string{"x-goog-request-id", "x-request-id", "traceparent"} {
		if value := strings.TrimSpace(headers.Get(name)); value != "" {
			return value
		}
	}
	return ""
}

func parseVertexError(body []byte) (message, reason string) {
	message = string(body)
	var payload map[string]any
	if json.Unmarshal(body, &payload) != nil {
		return message, ""
	}
	errorObject, _ := payload["error"].(map[string]any)
	if errorObject == nil {
		return message, ""
	}
	if value := strings.TrimSpace(stringField(errorObject["message"])); value != "" {
		message = value
	}
	if value := strings.TrimSpace(stringField(errorObject["status"])); value != "" {
		reason = value
	} else if value := strings.TrimSpace(stringField(errorObject["code"])); value != "" {
		reason = value
	}
	return message, reason
}

func PostVertex(
	ctx context.Context,
	client HTTPDoer,
	url string,
	payload map[string]any,
	tokenProvider VertexTokenProvider,
	sleep VertexSleepFunc,
) (map[string]any, error) {
	if client == nil {
		client = http.DefaultClient
	}
	if tokenProvider == nil {
		return nil, fmt.Errorf("vertex token provider is required")
	}
	if sleep == nil {
		sleep = defaultVertexSleep
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	var lastNetworkError error
	for attempt := 0; attempt < 3; attempt++ {
		token, err := tokenProvider(ctx, attempt == 1)
		if err != nil {
			return nil, err
		}
		request, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(encoded))
		if err != nil {
			return nil, err
		}
		request.Header.Set("Authorization", "Bearer "+token)
		request.Header.Set("Content-Type", "application/json")

		response, err := client.Do(request)
		if err != nil {
			lastNetworkError = err
			if attempt < 2 {
				if err := sleep(ctx, time.Duration(attempt+1)*1500*time.Millisecond); err != nil {
					return nil, err
				}
				continue
			}
			return nil, &VertexHTTPError{
				Message: fmt.Sprintf("Lỗi mạng khi gọi Vertex: %T", err),
				Reason:  "NETWORK_ERROR",
			}
		}

		body, readErr := io.ReadAll(response.Body)
		_ = response.Body.Close()
		if readErr != nil {
			lastNetworkError = readErr
			if attempt < 2 {
				if err := sleep(ctx, time.Duration(attempt+1)*1500*time.Millisecond); err != nil {
					return nil, err
				}
				continue
			}
			return nil, &VertexHTTPError{
				Message: fmt.Sprintf("Lỗi mạng khi gọi Vertex: %T", readErr),
				Reason:  "NETWORK_ERROR",
			}
		}

		if response.StatusCode == http.StatusUnauthorized && attempt == 0 {
			continue
		}
		if isRetryableVertexStatus(response.StatusCode) && attempt < 2 {
			if err := sleep(ctx, time.Duration(attempt+1)*1500*time.Millisecond); err != nil {
				return nil, err
			}
			continue
		}
		if response.StatusCode >= 400 {
			requestID := vertexRequestID(response.Header)
			errorMessage, reason := parseVertexError(body)
			suffix := ""
			if requestID != "" {
				suffix = "; request_id=" + requestID
			}
			return nil, &VertexHTTPError{
				Message: fmt.Sprintf(
					"Vertex HTTP %d: %s%s",
					response.StatusCode,
					truncateRunes(errorMessage, 500),
					suffix,
				),
				StatusCode: response.StatusCode,
				Reason:     reason,
				RequestID:  requestID,
			}
		}

		var result map[string]any
		if err := json.Unmarshal(body, &result); err != nil {
			return nil, fmt.Errorf("Vertex trả về JSON không hợp lệ")
		}
		return result, nil
	}

	return nil, fmt.Errorf("Vertex request thất bại: %v", lastNetworkError)
}

func isRetryableVertexStatus(statusCode int) bool {
	switch statusCode {
	case 429, 500, 502, 503, 504:
		return true
	default:
		return false
	}
}
