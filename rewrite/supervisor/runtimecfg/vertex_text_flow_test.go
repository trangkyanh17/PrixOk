package runtimecfg

import (
	"strings"
	"testing"
	"time"
)

func vertexPayload(text, finishReason string) map[string]any {
	parts := []any{}
	if text != "" {
		parts = append(parts, map[string]any{"text": text, "thoughtSignature": "hidden"})
	}
	return map[string]any{
		"candidates": []any{
			map[string]any{
				"finishReason": finishReason,
				"content": map[string]any{
					"role":  "model",
					"parts": parts,
				},
			},
		},
	}
}

func TestVertexTextFlowCompletesNormalResponse(t *testing.T) {
	state := NewVertexTextFlowState(4)
	result, err := state.Process(vertexPayload("hello", "STOP"))
	if err != nil {
		t.Fatal(err)
	}
	if result.Action != VertexTextDone || result.Text != "hello" || result.FinishReason != "STOP" {
		t.Fatalf("result=%+v", result)
	}
}

func TestVertexTextFlowContinuesMaxTokensAndMergesOverlap(t *testing.T) {
	state := NewVertexTextFlowState(2)
	first, err := state.Process(vertexPayload("prefix ABCDEFGHIJKLMNOPQRSTUVWXYZ", "MAX_TOKENS"))
	if err != nil {
		t.Fatal(err)
	}
	if first.Action != VertexTextContinue || first.ContinuationRounds != 1 || len(first.AppendContents) != 2 {
		t.Fatalf("first=%+v", first)
	}
	promptParts, ok := first.AppendContents[1]["parts"].([]any)
	if !ok || len(promptParts) != 1 || promptParts[0].(map[string]any)["text"] != VertexContinuationPrompt {
		t.Fatalf("append=%v", first.AppendContents)
	}

	second, err := state.Process(vertexPayload("abcdefghijklmnopqrstuvwxyz suffix", "STOP"))
	if err != nil {
		t.Fatal(err)
	}
	if second.Action != VertexTextDone || second.Text != "prefix ABCDEFGHIJKLMNOPQRSTUVWXYZ suffix" {
		t.Fatalf("second=%+v", second)
	}
}

func TestVertexTextFlowStopsAfterContinuationLimit(t *testing.T) {
	state := NewVertexTextFlowState(1)
	first, err := state.Process(vertexPayload("one", "MAX_TOKENS"))
	if err != nil || first.Action != VertexTextContinue {
		t.Fatalf("first=%+v err=%v", first, err)
	}
	second, err := state.Process(vertexPayload("two", "MAX_TOKENS"))
	if err != nil {
		t.Fatal(err)
	}
	if second.Action != VertexTextDone || second.ContinuationRounds != 1 || second.Text != "one two" {
		t.Fatalf("second=%+v", second)
	}
}

func TestVertexTextFlowRetriesEmptyTextTwice(t *testing.T) {
	state := NewVertexTextFlowState(4)
	payload := map[string]any{
		"candidates": []any{
			map[string]any{
				"finishReason": "STOP",
				"content": map[string]any{
					"parts": []any{
						map[string]any{"functionCall": map[string]any{"name": "tool"}, "thoughtSignature": "hidden"},
					},
				},
			},
		},
	}
	first, err := state.Process(payload)
	if err != nil || first.Action != VertexTextRetryEmpty || first.RetryDelay != 350*time.Millisecond {
		t.Fatalf("first=%+v err=%v", first, err)
	}
	if len(first.PartKeys) != 1 || first.PartKeys[0] != "functionCall" {
		t.Fatalf("keys=%v", first.PartKeys)
	}
	second, err := state.Process(payload)
	if err != nil || second.Action != VertexTextRetryEmpty || second.RetryDelay != 700*time.Millisecond {
		t.Fatalf("second=%+v err=%v", second, err)
	}
	_, err = state.Process(payload)
	if err == nil || !strings.Contains(err.Error(), "không trả về nội dung văn bản") {
		t.Fatalf("third err=%v", err)
	}
}

func TestVertexTextFlowSuccessfulChunkResetsEmptyRetryCounter(t *testing.T) {
	state := NewVertexTextFlowState(4)
	empty := vertexPayload("", "STOP")
	_, _ = state.Process(empty)
	if state.EmptyTextRetries != 1 {
		t.Fatalf("retries=%d", state.EmptyTextRetries)
	}
	result, err := state.Process(vertexPayload("ok", "STOP"))
	if err != nil || result.Action != VertexTextDone || state.EmptyTextRetries != 0 {
		t.Fatalf("result=%+v err=%v retries=%d", result, err, state.EmptyTextRetries)
	}
}

func TestVertexTextFlowReportsBlockedPrompt(t *testing.T) {
	state := NewVertexTextFlowState(4)
	_, err := state.Process(map[string]any{
		"promptFeedback": map[string]any{"blockReason": "SAFETY"},
	})
	if err == nil || !strings.Contains(err.Error(), "SAFETY") {
		t.Fatalf("err=%v", err)
	}
}
