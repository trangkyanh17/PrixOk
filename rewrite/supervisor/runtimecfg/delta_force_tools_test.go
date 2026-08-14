package runtimecfg

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func TestDeltaForceRegistryInvokesNativeCommands(t *testing.T) {
	calls := 0
	invoker := func(
		_ context.Context,
		binaryPath string,
		command string,
		dbPath string,
		requestJSON []byte,
	) ([]byte, error) {
		calls++
		if binaryPath != "/tmp/atri-native" || dbPath != "/tmp/delta.sqlite3" {
			t.Fatalf("paths binary=%q db=%q", binaryPath, dbPath)
		}
		var request map[string]any
		if err := json.Unmarshal(requestJSON, &request); err != nil {
			t.Fatal(err)
		}
		switch command {
		case "delta-search":
			if request["query"] != "M4" || request["season"] != float64(5) || request["limit"] != float64(12) {
				t.Fatalf("search request=%v", request)
			}
		case "delta-history":
			if request["season_from"] != float64(1) || request["season_to"] != float64(10) || request["limit"] != float64(16) {
				t.Fatalf("history request=%v", request)
			}
		case "delta-compare":
			if request["season_a"] != float64(1) || request["season_b"] != float64(10) || request["limit"] != float64(5) {
				t.Fatalf("compare request=%v", request)
			}
		default:
			t.Fatalf("command=%q", command)
		}
		return []byte(`{"ok":true,"region":"cn","command":"` + command + `"}`), nil
	}

	registry := NewToolRegistry()
	runtime := DeltaForceToolRuntime{
		BinaryPath: "/tmp/atri-native",
		DBPath:     "/tmp/delta.sqlite3",
		Invoker:    invoker,
	}
	if err := RegisterDeltaForceTools(registry, runtime); err != nil {
		t.Fatal(err)
	}

	search := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"search_delta_force_cn",
		map[string]any{
			"query":    "M4",
			"season":   5,
			"category": "weapon",
			"mode":     "operations",
			"platform": "pc",
			"limit":    99,
		},
		false,
	).(map[string]any)
	if search["ok"] != true || search["command"] != "delta-search" {
		t.Fatalf("search=%v", search)
	}

	history := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"get_delta_force_cn_history",
		map[string]any{"query": "M4"},
		false,
	).(map[string]any)
	if history["ok"] != true || history["command"] != "delta-history" {
		t.Fatalf("history=%v", history)
	}

	compare := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"compare_delta_force_cn_seasons",
		map[string]any{"query": "M4", "season_a": 1, "season_b": 10},
		false,
	).(map[string]any)
	if compare["ok"] != true || compare["command"] != "delta-compare" {
		t.Fatalf("compare=%v", compare)
	}
	if calls != 3 {
		t.Fatalf("calls=%d", calls)
	}
}

func TestDeltaForceRegistryDefaultsAndDeclarations(t *testing.T) {
	var captured map[string]any
	runtime := DeltaForceToolRuntime{
		Invoker: func(
			_ context.Context,
			binaryPath string,
			command string,
			dbPath string,
			requestJSON []byte,
		) ([]byte, error) {
			if binaryPath != DefaultDeltaForceNativeBinary || dbPath != DefaultDeltaForceCNDBPath {
				t.Fatalf("defaults binary=%q db=%q", binaryPath, dbPath)
			}
			if command != "delta-search" {
				t.Fatalf("command=%q", command)
			}
			if err := json.Unmarshal(requestJSON, &captured); err != nil {
				t.Fatal(err)
			}
			return []byte(`{"ok":true,"region":"cn"}`), nil
		},
	}
	registry := NewToolRegistry()
	if err := RegisterDeltaForceTools(registry, runtime); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{
		"search_delta_force_cn",
		"get_delta_force_cn_history",
		"compare_delta_force_cn_seasons",
	} {
		if !registry.Has(name) {
			t.Fatalf("missing tool %s", name)
		}
	}
	if declarations := registry.Declarations("chat", false); len(declarations) != 3 {
		t.Fatalf("declarations=%v", declarations)
	}

	result := registry.Execute(
		context.Background(),
		ToolContext{Mode: "chat"},
		"search_delta_force_cn",
		map[string]any{"query": "M4"},
		false,
	).(map[string]any)
	if result["ok"] != true {
		t.Fatalf("result=%v", result)
	}
	if captured["limit"] != float64(8) {
		t.Fatalf("captured=%v", captured)
	}
	if _, ok := captured["season"]; ok {
		t.Fatalf("season should be omitted: %v", captured)
	}
}

func TestDeltaForceNativeFailuresAreSafeEnvelopes(t *testing.T) {
	runtime := DeltaForceToolRuntime{
		Invoker: func(context.Context, string, string, string, []byte) ([]byte, error) {
			return []byte("native stderr"), errors.New("exit 1")
		},
	}
	result := runtime.invoke(context.Background(), "delta-search", map[string]any{"query": "M4"})
	if result["ok"] != false || result["region"] != "cn" || !strings.Contains(result["error"].(string), "native stderr") {
		t.Fatalf("result=%v", result)
	}

	runtime.Invoker = func(context.Context, string, string, string, []byte) ([]byte, error) {
		return []byte("not-json"), nil
	}
	invalid := runtime.invoke(context.Background(), "delta-search", map[string]any{"query": "M4"})
	if invalid["ok"] != false || invalid["region"] != "cn" || !strings.Contains(invalid["error"].(string), "JSON") {
		t.Fatalf("invalid=%v", invalid)
	}
}
