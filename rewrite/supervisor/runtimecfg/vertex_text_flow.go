package runtimecfg

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

const VertexContinuationPrompt = "Tiếp tục chính xác câu trả lời đang dang dở ngay sau ký tự cuối. Không mở đầu lại, không xin lỗi, không nhắc lại phần đã viết và hãy hoàn tất đầy đủ phần còn lại."

type VertexTextFlowAction string

const (
	VertexTextDone       VertexTextFlowAction = "done"
	VertexTextContinue   VertexTextFlowAction = "continue"
	VertexTextRetryEmpty VertexTextFlowAction = "retry_empty"
)

type VertexTextFlowState struct {
	ResponseText          string
	ContinuationRounds    int
	EmptyTextRetries      int
	MaxContinuationRounds int
	MaxEmptyTextRetries   int
}

type VertexTextFlowResult struct {
	Action             VertexTextFlowAction
	Text               string
	FinishReason       string
	RetryDelay         time.Duration
	PartKeys           []string
	ContinuationRounds int
	AppendContents     []map[string]any
}

func NewVertexTextFlowState(maxContinuationRounds int) *VertexTextFlowState {
	if maxContinuationRounds < 1 {
		maxContinuationRounds = 1
	}
	return &VertexTextFlowState{
		MaxContinuationRounds: maxContinuationRounds,
		MaxEmptyTextRetries:   2,
	}
}

func vertexModelContent(payload map[string]any) map[string]any {
	candidate := firstVertexCandidate(payload)
	if candidate == nil {
		return nil
	}
	return anyMap(candidate["content"])
}

func vertexPartKeys(payload map[string]any) []string {
	seen := map[string]bool{}
	for _, value := range vertexTextParts(payload) {
		part := anyMap(value)
		for key := range part {
			if key != "thoughtSignature" {
				seen[key] = true
			}
		}
	}
	keys := make([]string, 0, len(seen))
	for key := range seen {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func (state *VertexTextFlowState) Process(payload map[string]any) (VertexTextFlowResult, error) {
	if state.MaxContinuationRounds < 1 {
		state.MaxContinuationRounds = 1
	}
	if state.MaxEmptyTextRetries < 1 {
		state.MaxEmptyTextRetries = 2
	}

	candidate := firstVertexCandidate(payload)
	if candidate == nil {
		feedback := anyMap(payload["promptFeedback"])
		if feedback != nil {
			blockReason := strings.TrimSpace(stringField(feedback["blockReason"]))
			if blockReason != "" {
				return VertexTextFlowResult{}, fmt.Errorf("Vertex đã chặn yêu cầu: %s", blockReason)
			}
		}
		return VertexTextFlowResult{}, fmt.Errorf("Vertex không trả về candidate")
	}

	finishReason := CandidateFinishReason(payload)
	chunk, err := ExtractVertexText(payload)
	if err != nil {
		if !strings.Contains(err.Error(), "không trả về nội dung văn bản") || state.EmptyTextRetries >= state.MaxEmptyTextRetries {
			return VertexTextFlowResult{}, err
		}
		state.EmptyTextRetries++
		return VertexTextFlowResult{
			Action:       VertexTextRetryEmpty,
			FinishReason: finishReason,
			RetryDelay:   time.Duration(float64(time.Second) * 0.35 * float64(state.EmptyTextRetries)),
			PartKeys:     vertexPartKeys(payload),
		}, nil
	}

	state.EmptyTextRetries = 0
	state.ResponseText = MergeResponseText(state.ResponseText, chunk)

	if finishReason == "MAX_TOKENS" && state.ContinuationRounds < state.MaxContinuationRounds {
		state.ContinuationRounds++
		modelContent := vertexModelContent(payload)
		appendContents := []map[string]any{}
		if modelContent != nil {
			appendContents = append(appendContents, modelContent)
		}
		appendContents = append(appendContents, map[string]any{
			"role": "user",
			"parts": []any{
				map[string]any{"text": VertexContinuationPrompt},
			},
		})
		return VertexTextFlowResult{
			Action:             VertexTextContinue,
			FinishReason:       finishReason,
			ContinuationRounds: state.ContinuationRounds,
			AppendContents:     appendContents,
		}, nil
	}

	return VertexTextFlowResult{
		Action:             VertexTextDone,
		Text:               CleanPublicAnswer(state.ResponseText),
		FinishReason:       finishReason,
		ContinuationRounds: state.ContinuationRounds,
	}, nil
}
