package runtimecfg

import (
	"context"
	"errors"
	"reflect"
	"testing"
)

func testToolDeclaration(name string) map[string]any {
	return map[string]any{
		"name":        name,
		"description": "test",
		"parameters": map[string]any{
			"type": "object",
		},
	}
}

func TestToolRegistryRegisterAndDeterministicDeclarations(t *testing.T) {
	registry := NewToolRegistry()
	registry.MustRegister(RegisteredTool{
		Name:        "z_tool",
		Declaration: testToolDeclaration("z_tool"),
		Privacy:     ToolPrivacyPublic,
		Modes:       []string{"chat"},
		Executor: func(context.Context, ToolContext, map[string]any) (any, error) {
			return map[string]any{"ok": true}, nil
		},
	})
	registry.MustRegister(RegisteredTool{
		Name:        "a_tool",
		Declaration: testToolDeclaration("a_tool"),
		Privacy:     ToolPrivacyPublic,
		Modes:       []string{"chat"},
		Executor: func(context.Context, ToolContext, map[string]any) (any, error) {
			return map[string]any{"ok": true}, nil
		},
	})
	declarations := registry.Declarations("chat", false)
	if len(declarations) != 2 || declarations[0]["name"] != "a_tool" || declarations[1]["name"] != "z_tool" {
		t.Fatalf("declarations=%v", declarations)
	}
	declarations[0]["name"] = "mutated"
	if got := registry.Declarations("chat", false)[0]["name"]; got != "a_tool" {
		t.Fatalf("registry declaration aliased: %v", got)
	}
}

func TestToolRegistryRejectsDuplicateAndInvalidTools(t *testing.T) {
	registry := NewToolRegistry()
	base := RegisteredTool{
		Name:        "tool",
		Declaration: testToolDeclaration("tool"),
		Executor: func(context.Context, ToolContext, map[string]any) (any, error) {
			return nil, nil
		},
	}
	if err := registry.Register(base); err != nil {
		t.Fatal(err)
	}
	if err := registry.Register(base); err == nil {
		t.Fatal("duplicate tool should fail")
	}
	if err := registry.Register(RegisteredTool{Name: "", Declaration: testToolDeclaration("x"), Executor: base.Executor}); err == nil {
		t.Fatal("empty name should fail")
	}
	if err := registry.Register(RegisteredTool{Name: "missing-declaration", Executor: base.Executor}); err == nil {
		t.Fatal("missing declaration should fail")
	}
	if err := registry.Register(RegisteredTool{Name: "missing-executor", Declaration: testToolDeclaration("x")}); err == nil {
		t.Fatal("missing executor should fail")
	}
}

func TestToolRegistryFiltersModeAndPrivacy(t *testing.T) {
	registry := NewToolRegistry()
	for _, tool := range []RegisteredTool{
		{
			Name:        "public_chat",
			Declaration: testToolDeclaration("public_chat"),
			Privacy:     ToolPrivacyPublic,
			Modes:       []string{"chat"},
			Executor: func(context.Context, ToolContext, map[string]any) (any, error) { return nil, nil },
		},
		{
			Name:        "private_chat",
			Declaration: testToolDeclaration("private_chat"),
			Privacy:     ToolPrivacyPrivate,
			Modes:       []string{"chat"},
			Executor: func(context.Context, ToolContext, map[string]any) (any, error) { return nil, nil },
		},
		{
			Name:        "code_only",
			Declaration: testToolDeclaration("code_only"),
			Privacy:     ToolPrivacyPublic,
			Modes:       []string{"code"},
			Executor: func(context.Context, ToolContext, map[string]any) (any, error) { return nil, nil },
		},
	} {
		registry.MustRegister(tool)
	}

	publicChat := registry.Declarations("chat", false)
	if len(publicChat) != 1 || publicChat[0]["name"] != "public_chat" {
		t.Fatalf("public chat=%v", publicChat)
	}
	privateChat := registry.Declarations("chat", true)
	if len(privateChat) != 2 || privateChat[0]["name"] != "private_chat" || privateChat[1]["name"] != "public_chat" {
		t.Fatalf("private chat=%v", privateChat)
	}
	code := registry.Declarations("code", false)
	if len(code) != 1 || code[0]["name"] != "code_only" {
		t.Fatalf("code=%v", code)
	}
}

func TestToolRegistryExecuteClonesArgumentsAndReturnsValue(t *testing.T) {
	registry := NewToolRegistry()
	original := map[string]any{
		"nested": map[string]any{"value": "original"},
	}
	registry.MustRegister(RegisteredTool{
		Name:        "mutator",
		Declaration: testToolDeclaration("mutator"),
		Privacy:     ToolPrivacyPublic,
		Executor: func(ctx context.Context, toolContext ToolContext, arguments map[string]any) (any, error) {
			if toolContext.UserID != 7 || toolContext.ChatID != 8 || toolContext.ThreadID != 9 {
				t.Fatalf("context=%+v", toolContext)
			}
			arguments["nested"].(map[string]any)["value"] = "changed"
			return map[string]any{"ok": true, "answer": 42}, nil
		},
	})
	result := registry.Execute(
		context.Background(),
		ToolContext{UserID: 7, ChatID: 8, ThreadID: 9, Mode: "chat"},
		"mutator",
		original,
		false,
	)
	if !reflect.DeepEqual(result, map[string]any{"ok": true, "answer": 42}) {
		t.Fatalf("result=%v", result)
	}
	if original["nested"].(map[string]any)["value"] != "original" {
		t.Fatalf("arguments mutated: %v", original)
	}
}

func TestToolRegistryExecuteConvertsErrorsAndEnforcesPolicy(t *testing.T) {
	registry := NewToolRegistry()
	registry.MustRegister(RegisteredTool{
		Name:        "private_tool",
		Declaration: testToolDeclaration("private_tool"),
		Privacy:     ToolPrivacyPrivate,
		Modes:       []string{"chat"},
		Executor: func(context.Context, ToolContext, map[string]any) (any, error) {
			return nil, errors.New("boom")
		},
	})

	blocked := registry.Execute(context.Background(), ToolContext{Mode: "chat"}, "private_tool", nil, false).(map[string]any)
	if blocked["ok"] != false || blocked["error"] == "" {
		t.Fatalf("blocked=%v", blocked)
	}
	wrongMode := registry.Execute(context.Background(), ToolContext{Mode: "code"}, "private_tool", nil, true).(map[string]any)
	if wrongMode["ok"] != false {
		t.Fatalf("wrong mode=%v", wrongMode)
	}
	executorError := registry.Execute(context.Background(), ToolContext{Mode: "chat"}, "private_tool", nil, true).(map[string]any)
	if executorError["ok"] != false || executorError["error"] == "" {
		t.Fatalf("executor error=%v", executorError)
	}
	unknown := registry.Execute(context.Background(), ToolContext{Mode: "chat"}, "missing", nil, true).(map[string]any)
	if unknown["ok"] != false {
		t.Fatalf("unknown=%v", unknown)
	}
}

func TestToolRegistryVertexExecutorBridge(t *testing.T) {
	registry := NewToolRegistry()
	registry.MustRegister(RegisteredTool{
		Name:        "echo",
		Declaration: testToolDeclaration("echo"),
		Privacy:     ToolPrivacyPublic,
		Executor: func(ctx context.Context, toolContext ToolContext, arguments map[string]any) (any, error) {
			return map[string]any{"ok": true, "value": arguments["value"], "user": toolContext.UserID}, nil
		},
	})
	executor := registry.VertexExecutor(ToolContext{UserID: 99, Mode: "chat"}, false)
	result, err := executor(context.Background(), "echo", map[string]any{"value": "hi"})
	if err != nil {
		t.Fatal(err)
	}
	value := result.(map[string]any)
	if value["ok"] != true || value["value"] != "hi" || value["user"] != int64(99) {
		t.Fatalf("result=%v", value)
	}
}

func TestToolRegistryNilResultBecomesSuccessEnvelope(t *testing.T) {
	registry := NewToolRegistry()
	registry.MustRegister(RegisteredTool{
		Name:        "empty",
		Declaration: testToolDeclaration("empty"),
		Privacy:     ToolPrivacyPublic,
		Executor: func(context.Context, ToolContext, map[string]any) (any, error) {
			return nil, nil
		},
	})
	result := registry.Execute(context.Background(), ToolContext{Mode: "chat"}, "empty", nil, false)
	if !reflect.DeepEqual(result, map[string]any{"ok": true}) {
		t.Fatalf("result=%v", result)
	}
}
