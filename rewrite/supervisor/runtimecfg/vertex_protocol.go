package runtimecfg

import (
	"fmt"
	"strings"
)

type GroundingSource struct {
	Title string
	URI   string
}

func anySlice(value any) []any {
	if values, ok := value.([]any); ok {
		return values
	}
	return nil
}

func anyMap(value any) map[string]any {
	if object, ok := value.(map[string]any); ok {
		return object
	}
	return nil
}

func firstVertexCandidate(payload map[string]any) map[string]any {
	candidates := anySlice(payload["candidates"])
	if len(candidates) == 0 {
		return nil
	}
	return anyMap(candidates[0])
}

func vertexTextParts(payload map[string]any) []any {
	candidate := firstVertexCandidate(payload)
	if candidate == nil {
		return nil
	}
	content := anyMap(candidate["content"])
	if content == nil {
		return nil
	}
	return anySlice(content["parts"])
}

func ExtractVertexText(payload map[string]any) (string, error) {
	candidate := firstVertexCandidate(payload)
	if candidate == nil {
		feedback := anyMap(payload["promptFeedback"])
		if feedback != nil {
			blockReason := strings.TrimSpace(stringField(feedback["blockReason"]))
			if blockReason != "" {
				return "", fmt.Errorf("Vertex đã chặn yêu cầu: %s", blockReason)
			}
		}
		return "", fmt.Errorf("Vertex không trả về candidate")
	}

	chunks := []string{}
	for _, value := range vertexTextParts(payload) {
		part := anyMap(value)
		if part == nil {
			continue
		}
		text := strings.TrimSpace(stringField(part["text"]))
		if text != "" {
			chunks = append(chunks, text)
		}
	}
	text := strings.TrimSpace(strings.Join(chunks, "\n"))
	if text == "" {
		return "", fmt.Errorf("Vertex không trả về nội dung văn bản")
	}
	return text, nil
}

func ExtractVertexOptionalText(payload map[string]any) string {
	if firstVertexCandidate(payload) == nil {
		return ""
	}
	chunks := []string{}
	for _, value := range vertexTextParts(payload) {
		part := anyMap(value)
		if part == nil {
			continue
		}
		text := strings.TrimSpace(stringField(part["text"]))
		if text != "" {
			chunks = append(chunks, text)
		}
	}
	return strings.TrimSpace(strings.Join(chunks, "\n"))
}

func ExtractGroundingData(payload map[string]any) ([]GroundingSource, []string) {
	candidate := firstVertexCandidate(payload)
	if candidate == nil {
		return nil, nil
	}
	metadata := anyMap(candidate["groundingMetadata"])
	if metadata == nil {
		return nil, nil
	}

	sources := []GroundingSource{}
	seen := map[string]bool{}
	for _, value := range anySlice(metadata["groundingChunks"]) {
		chunk := anyMap(value)
		if chunk == nil {
			continue
		}
		web := anyMap(chunk["web"])
		if web == nil {
			continue
		}
		uri := strings.TrimSpace(stringField(web["uri"]))
		if uri == "" || seen[uri] {
			continue
		}
		title := strings.TrimSpace(stringField(web["title"]))
		if title == "" {
			title = strings.TrimSpace(stringField(web["domain"]))
		}
		if title == "" {
			title = uri
		}
		seen[uri] = true
		sources = append(sources, GroundingSource{Title: title, URI: uri})
		if len(sources) >= 6 {
			break
		}
	}

	queries := []string{}
	querySeen := map[string]bool{}
	for _, value := range anySlice(metadata["webSearchQueries"]) {
		query := strings.TrimSpace(stringField(value))
		if query == "" || querySeen[query] {
			continue
		}
		querySeen[query] = true
		queries = append(queries, query)
		if len(queries) >= 6 {
			break
		}
	}
	return sources, queries
}

func ExtractVertexFunctionCalls(payload map[string]any) []map[string]any {
	calls := []map[string]any{}
	for _, value := range vertexTextParts(payload) {
		part := anyMap(value)
		if part == nil {
			continue
		}
		call := anyMap(part["functionCall"])
		if call != nil {
			calls = append(calls, call)
		}
	}
	return calls
}

func VertexSafeToolResult(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		result := make(map[string]any, len(typed))
		for key, item := range typed {
			safeKey := key
			if strings.HasPrefix(safeKey, "$") {
				safeKey = "jsonschema_" + strings.TrimPrefix(safeKey, "$")
			}
			result[safeKey] = VertexSafeToolResult(item)
		}
		return result
	case []any:
		result := make([]any, 0, len(typed))
		for _, item := range typed {
			result = append(result, VertexSafeToolResult(item))
		}
		return result
	default:
		return value
	}
}
