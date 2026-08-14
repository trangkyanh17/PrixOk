package runtimecfg

import (
	"context"
	"reflect"
	"testing"
)

func TestBuildVertexPayloadClonesInputsAndBuildsTools(t *testing.T) {
	contents := []any{
		map[string]any{
			"role": "user",
			"parts": []any{
				map[string]any{"text": "hello"},
			},
		},
	}
	generation := map[string]any{
		"temperature":     0.7,
		"maxOutputTokens": 4096,
	}
	declaration := map[string]any{
		"name": "weather",
		"parameters": map[string]any{
			"type": "object",
		},
	}
	payload := BuildVertexPayload(VertexPayloadOptions{
		SystemInstruction:    " system ",
		Contents:             contents,
		GenerationConfig:     generation,
		ToolDeclarations:     []map[string]any{declaration},
		FunctionCallingMode:  "any",
		AllowedFunctionNames: []string{"weather", "  "},
	})

	system := payload["systemInstruction"].(map[string]any)
	parts := system["parts"].([]any)
	if parts[0].(map[string]any)["text"] != "system" {
		t.Fatalf("system=%v", system)
	}
	if !reflect.DeepEqual(payload["generationConfig"], generation) {
		t.Fatalf("generation=%v", payload["generationConfig"])
	}
	tools := payload["tools"].([]any)
	declarations := tools[0].(map[string]any)["functionDeclarations"].([]any)
	if len(declarations) != 1 || declarations[0].(map[string]any)["name"] != "weather" {
		t.Fatalf("tools=%v", tools)
	}
	config := payload["toolConfig"].(map[string]any)["functionCallingConfig"].(map[string]any)
	if config["mode"] != "ANY" {
		t.Fatalf("config=%v", config)
	}
	allowed := config["allowedFunctionNames"].([]any)
	if len(allowed) != 1 || allowed[0] != "weather" {
		t.Fatalf("allowed=%v", allowed)
	}

	contents[0].(map[string]any)["role"] = "mutated"
	generation["temperature"] = 1.0
	declaration["name"] = "mutated"
	if payload["contents"].([]any)[0].(map[string]any)["role"] != "user" {
		t.Fatal("contents aliased")
	}
	if payload["generationConfig"].(map[string]any)["temperature"] != 0.7 {
		t.Fatal("generation config aliased")
	}
	if declarations[0].(map[string]any)["name"] != "weather" {
		t.Fatal("tool declaration aliased")
	}
}

func TestBuildVertexPayloadOmitsEmptyOptionalSections(t *testing.T) {
	payload := BuildVertexPayload(VertexPayloadOptions{
		Contents: []any{},
	})
	if _, ok := payload["systemInstruction"]; ok {
		t.Fatal("empty system instruction should be omitted")
	}
	if _, ok := payload["generationConfig"]; ok {
		t.Fatal("empty generation config should be omitted")
	}
	if _, ok := payload["tools"]; ok {
		t.Fatal("empty tools should be omitted")
	}
	if _, ok := payload["toolConfig"]; ok {
		t.Fatal("empty tool config should be omitted")
	}
}

func TestBuildVertexPayloadDefaultsFunctionCallingModeToAuto(t *testing.T) {
	payload := BuildVertexPayload(VertexPayloadOptions{
		Contents: []any{},
		ToolDeclarations: []map[string]any{
			testToolDeclaration("echo"),
		},
	})
	config := payload["toolConfig"].(map[string]any)["functionCallingConfig"].(map[string]any)
	if config["mode"] != "AUTO" {
		t.Fatalf("config=%v", config)
	}
	if _, ok := config["allowedFunctionNames"]; ok {
		t.Fatal("allowed names should be omitted")
	}
}

func TestBuildRegistryVertexPayloadUsesModePrivacyFiltering(t *testing.T) {
	registry := NewToolRegistry()
	registry.MustRegister(RegisteredTool{
		Name:        "public_chat",
		Declaration: testToolDeclaration("public_chat"),
		Privacy:     ToolPrivacyPublic,
		Modes:       []string{"chat"},
		Executor: func(context.Context, ToolContext, map[string]any) (any, error) {
			return map[string]any{"ok": true}, nil
		},
	})
	registry.MustRegister(RegisteredTool{
		Name:        "private_chat",
		Declaration: testToolDeclaration("private_chat"),
		Privacy:     ToolPrivacyPrivate,
		Modes:       []string{"chat"},
		Executor: func(context.Context, ToolContext, map[string]any) (any, error) {
			return map[string]any{"ok": true}, nil
		},
	})

	publicPayload := BuildRegistryVertexPayload(
		registry,
		"chat",
		false,
		"system",
		[]any{},
		map[string]any{"temperature": 0.2},
	)
	publicTools := publicPayload["tools"].([]any)[0].(map[string]any)["functionDeclarations"].([]any)
	if len(publicTools) != 1 || publicTools[0].(map[string]any)["name"] != "public_chat" {
		t.Fatalf("public tools=%v", publicTools)
	}

	privatePayload := BuildRegistryVertexPayload(
		registry,
		"chat",
		true,
		"system",
		[]any{},
		nil,
	)
	privateTools := privatePayload["tools"].([]any)[0].(map[string]any)["functionDeclarations"].([]any)
	if len(privateTools) != 2 {
		t.Fatalf("private tools=%v", privateTools)
	}
}
