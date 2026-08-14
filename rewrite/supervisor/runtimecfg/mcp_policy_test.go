package runtimecfg

import (
	"reflect"
	"strings"
	"testing"
)

func TestMCPExplicitAndSelectedPlugins(t *testing.T) {
	cases := map[string]string{
		"use GitHub MCP to inspect this repo": "github",
		"check chrome devtools network":       "chrome-devtools",
		"read Context 7 docs":                 "context7",
		"run semgrep":                         "semgrep",
	}
	for query, expected := range cases {
		if got := MCPExplicitPlugin(query); got != expected {
			t.Fatalf("explicit plugin query=%q got=%q want=%q", query, got, expected)
		}
	}
	if got := MCPSelectedPlugins("find a symbol and current API docs"); !reflect.DeepEqual(got, []string{"serena", "context7"}) {
		t.Fatalf("selected=%v", got)
	}
	if got := MCPSelectedPlugins("nothing matches here"); !reflect.DeepEqual(got, []string{"serena", "context7", "semgrep"}) {
		t.Fatalf("fallback=%v", got)
	}
}

func TestMCPSanitizeSchemaResolvesRefsAndDropsSchemaMetadata(t *testing.T) {
	schema := map[string]any{
		"$schema": "https://json-schema.org/draft/2020-12/schema",
		"$defs": map[string]any{
			"Target": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"path": map[string]any{"type": "string"},
				},
			},
		},
		"type": "object",
		"properties": map[string]any{
			"target": map[string]any{
				"$ref":        "#/$defs/Target",
				"description": "chosen target",
			},
			"note": map[string]any{
				"type":        "string",
				"description": "see #/$defs/Target",
			},
		},
	}

	clean := MCPSanitizeSchema(schema)
	if _, exists := clean["$schema"]; exists {
		t.Fatalf("$schema leaked: %v", clean)
	}
	if _, exists := clean["$defs"]; exists {
		t.Fatalf("$defs leaked: %v", clean)
	}
	properties := clean["properties"].(map[string]any)
	target := properties["target"].(map[string]any)
	if target["type"] != "object" || target["description"] != "chosen target" {
		t.Fatalf("target=%v", target)
	}
	if _, exists := target["$ref"]; exists {
		t.Fatalf("$ref leaked: %v", target)
	}
	note := properties["note"].(map[string]any)
	if !strings.Contains(note["description"].(string), "schema:Target") {
		t.Fatalf("note=%v", note)
	}
}

func TestMCPPolicyBlocksWritesSentryAndSensitivePaths(t *testing.T) {
	policy := MCPPolicy{}
	if allowed, _ := policy.ToolAllowed("github", "get_file_contents"); !allowed {
		t.Fatal("read tool should be allowed")
	}
	if allowed, reason := policy.ToolAllowed("github", "create_issue"); allowed || !strings.Contains(reason, "write-capable") {
		t.Fatalf("create_issue allowed=%v reason=%q", allowed, reason)
	}
	if allowed, reason := policy.ToolAllowed("sentry", "update_issue"); allowed || !strings.Contains(reason, "read-only") {
		t.Fatalf("sentry update allowed=%v reason=%q", allowed, reason)
	}
	if allowed, _ := (MCPPolicy{AllowWrite: true}).ToolAllowed("github", "create_issue"); !allowed {
		t.Fatal("write-enabled policy should allow create_issue")
	}
	if !MCPContainsSensitivePath(map[string]any{"path": "/app/.env"}) {
		t.Fatal(".env should be sensitive")
	}
	if !MCPContainsSensitivePath([]any{"safe", map[string]any{"file": "vertex-service-account.json"}}) {
		t.Fatal("service account path should be sensitive")
	}
	if MCPContainsSensitivePath(map[string]any{"path": "/app/bot/modules/atri_ai.py"}) {
		t.Fatal("normal source path should not be sensitive")
	}
}

func TestMCPScoreToolsAndDirectCatalog(t *testing.T) {
	tools := []MCPTool{
		{
			Plugin:      "github",
			Name:        "get_file_contents",
			Description: "Read repository file contents",
			InputSchema: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"path": map[string]any{"type": "string"},
					"ref": map[string]any{
						"type": "string",
						"enum": []any{"main", "dev"},
					},
				},
				"required": []any{"path"},
			},
		},
		{
			Plugin:      "github",
			Name:        "create_issue",
			Description: "Create repository issue",
			InputSchema: map[string]any{"type": "object"},
		},
		{
			Plugin:      "context7",
			Name:        "query_docs",
			Description: "Read current API documentation",
			InputSchema: map[string]any{"type": "object"},
		},
	}

	scored := MCPScoreTools("repository file", tools, 10)
	if len(scored) != 2 || scored[0].Name != "get_file_contents" {
		t.Fatalf("scored=%v", scored)
	}

	catalog := MCPBuildDirectCatalog("use github mcp", tools, MCPPolicy{}, 10)
	if catalog["ok"] != true || catalog["plugin"] != "github" || catalog["tool_count"] != 1 {
		t.Fatalf("catalog=%v", catalog)
	}
	contextText := catalog["context"].(string)
	if !strings.Contains(contextText, "get_file_contents") || strings.Contains(contextText, "create_issue") {
		t.Fatalf("context=%s", contextText)
	}
	if !strings.Contains(contextText, "path<string>:required") || !strings.Contains(contextText, "ref<string[main,dev]>:optional") {
		t.Fatalf("args missing: %s", contextText)
	}
}
