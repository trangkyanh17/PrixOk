package runtimecfg

import (
	"reflect"
	"strings"
	"testing"
)

func TestExtractVertexText(t *testing.T) {
	payload := map[string]any{
		"candidates": []any{
			map[string]any{
				"content": map[string]any{
					"parts": []any{
						map[string]any{"text": " one "},
						map[string]any{"thoughtSignature": "x"},
						map[string]any{"text": "two"},
					},
				},
			},
		},
	}
	text, err := ExtractVertexText(payload)
	if err != nil || text != "one\ntwo" {
		t.Fatalf("text=%q err=%v", text, err)
	}
	if optional := ExtractVertexOptionalText(payload); optional != "one\ntwo" {
		t.Fatalf("optional=%q", optional)
	}
}

func TestExtractVertexTextReportsBlockAndMissingCandidate(t *testing.T) {
	_, err := ExtractVertexText(map[string]any{
		"promptFeedback": map[string]any{"blockReason": "SAFETY"},
	})
	if err == nil || !strings.Contains(err.Error(), "SAFETY") {
		t.Fatalf("err=%v", err)
	}
	_, err = ExtractVertexText(map[string]any{})
	if err == nil || err.Error() != "Vertex không trả về candidate" {
		t.Fatalf("err=%v", err)
	}
}

func TestExtractVertexTextRejectsEmptyText(t *testing.T) {
	payload := map[string]any{
		"candidates": []any{
			map[string]any{"content": map[string]any{"parts": []any{map[string]any{"functionCall": map[string]any{"name": "x"}}}}},
		},
	}
	_, err := ExtractVertexText(payload)
	if err == nil || err.Error() != "Vertex không trả về nội dung văn bản" {
		t.Fatalf("err=%v", err)
	}
	if got := ExtractVertexOptionalText(payload); got != "" {
		t.Fatalf("optional=%q", got)
	}
}

func TestExtractGroundingDataDedupesAndBounds(t *testing.T) {
	chunks := []any{}
	queries := []any{}
	for index := 0; index < 8; index++ {
		uri := "https://example.com/" + string(rune('a'+index))
		chunks = append(chunks, map[string]any{
			"web": map[string]any{"uri": uri, "title": "Title " + string(rune('a'+index))},
		})
		queries = append(queries, "query "+string(rune('a'+index)))
	}
	chunks = append(chunks, map[string]any{"web": map[string]any{"uri": "https://example.com/a", "title": "duplicate"}})
	payload := map[string]any{
		"candidates": []any{
			map[string]any{
				"groundingMetadata": map[string]any{
					"groundingChunks":   chunks,
					"webSearchQueries": queries,
				},
			},
		},
	}
	sources, gotQueries := ExtractGroundingData(payload)
	if len(sources) != 6 || len(gotQueries) != 6 {
		t.Fatalf("sources=%v queries=%v", sources, gotQueries)
	}
	if sources[0].Title != "Title a" || sources[0].URI != "https://example.com/a" {
		t.Fatalf("first source=%+v", sources[0])
	}
}

func TestExtractGroundingDataFallsBackTitleToDomainAndURI(t *testing.T) {
	payload := map[string]any{
		"candidates": []any{
			map[string]any{
				"groundingMetadata": map[string]any{
					"groundingChunks": []any{
						map[string]any{"web": map[string]any{"uri": "https://a", "domain": "a.example"}},
						map[string]any{"web": map[string]any{"uri": "https://b"}},
					},
				},
			},
		},
	}
	sources, _ := ExtractGroundingData(payload)
	if len(sources) != 2 || sources[0].Title != "a.example" || sources[1].Title != "https://b" {
		t.Fatalf("sources=%v", sources)
	}
}

func TestExtractVertexFunctionCalls(t *testing.T) {
	payload := map[string]any{
		"candidates": []any{
			map[string]any{"content": map[string]any{"parts": []any{
				map[string]any{"text": "thinking"},
				map[string]any{"functionCall": map[string]any{"name": "tool_a", "args": map[string]any{"x": 1}}},
				map[string]any{"functionCall": map[string]any{"name": "tool_b"}},
			}}},
		},
	}
	calls := ExtractVertexFunctionCalls(payload)
	if len(calls) != 2 || calls[0]["name"] != "tool_a" || calls[1]["name"] != "tool_b" {
		t.Fatalf("calls=%v", calls)
	}
}

func TestVertexSafeToolResultRenamesJSONSchemaMetadataRecursively(t *testing.T) {
	input := map[string]any{
		"$ref": "#/defs/x",
		"nested": []any{
			map[string]any{"$defs": map[string]any{"x": 1}, "plain": true},
		},
	}
	got := VertexSafeToolResult(input)
	want := map[string]any{
		"jsonschema_ref": "#/defs/x",
		"nested": []any{
			map[string]any{"jsonschema_defs": map[string]any{"x": 1}, "plain": true},
		},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got=%#v want=%#v", got, want)
	}
}
