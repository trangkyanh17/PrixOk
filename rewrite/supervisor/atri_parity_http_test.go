package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAtriParityHandlerAcceptsDecisionWithoutExecutingAnything(t *testing.T) {
	ingress := newTelegramShadowIngress("127.0.0.1:18750", "")
	payload := atriParityEvent{
		Version:        atriParitySchemaVersion,
		Stage:          "route",
		RouteText:      "sửa code python",
		ActualMode:     "code",
		ForceGitHubMCP: false,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/atri/parity", bytes.NewReader(body))
	response := httptest.NewRecorder()
	ingress.handler().ServeHTTP(response, request)
	if response.Code != http.StatusAccepted {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	if ingress.parity.snapshot().RouteMatch != 1 {
		t.Fatalf("snapshot=%+v", ingress.parity.snapshot())
	}
}

func TestAtriParityHandlerRejectsUnknownFieldsAndSecretMismatch(t *testing.T) {
	ingress := newTelegramShadowIngress("127.0.0.1:18750", "secret")

	request := httptest.NewRequest(
		http.MethodPost,
		"/v1/atri/parity",
		bytes.NewBufferString(`{"version":1,"stage":"route","route_text":"hello","actual_mode":"chat","extra":true}`),
	)
	request.Header.Set("X-Atri-Shadow-Secret", "secret")
	response := httptest.NewRecorder()
	ingress.handler().ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("unknown field status=%d", response.Code)
	}

	request = httptest.NewRequest(
		http.MethodPost,
		"/v1/atri/parity",
		bytes.NewBufferString(`{"version":1,"stage":"route","route_text":"hello","actual_mode":"chat"}`),
	)
	response = httptest.NewRecorder()
	ingress.handler().ServeHTTP(response, request)
	if response.Code != http.StatusUnauthorized {
		t.Fatalf("secret mismatch status=%d", response.Code)
	}
}

func TestAtriParityHealthDoesNotExposePromptText(t *testing.T) {
	ingress := newTelegramShadowIngress("127.0.0.1:18750", "")
	_, _, err := ingress.parity.evaluate(atriParityEvent{
		Version:    atriParitySchemaVersion,
		Stage:      "route",
		RouteText:  "PRIVATE PROMPT MUST NOT APPEAR",
		ActualMode: "chat",
	})
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	response := httptest.NewRecorder()
	ingress.handler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d", response.Code)
	}
	if bytes.Contains(response.Body.Bytes(), []byte("PRIVATE PROMPT MUST NOT APPEAR")) {
		t.Fatalf("health leaked route text: %s", response.Body.String())
	}
	if !bytes.Contains(response.Body.Bytes(), []byte(`"route_match":1`)) {
		t.Fatalf("health missing parity counters: %s", response.Body.String())
	}
}
