package runtimecfg

import "strings"

var freePoolNonTextKeys = []string{
	"inlineData",
	"fileData",
	"functionCall",
	"functionResponse",
	"audio",
	"image",
}

func ExtractFreePoolTextParts(parts []map[string]any) (string, bool) {
	textParts := make([]string, 0, len(parts))
	hasNonText := false
	for _, part := range parts {
		if text, ok := part["text"].(string); ok && text != "" {
			textParts = append(textParts, text)
		}
		for _, key := range freePoolNonTextKeys {
			if _, ok := part[key]; ok {
				hasNonText = true
			}
		}
	}
	return strings.TrimSpace(strings.Join(textParts, "\n")), hasNonText
}

func BuildFreePoolMessages(
	systemInstruction string,
	history []map[string]any,
	currentParts []map[string]any,
) []map[string]string {
	currentText, currentNonText := ExtractFreePoolTextParts(currentParts)
	if currentNonText || currentText == "" {
		return nil
	}

	messages := []map[string]string{{
		"role":    "system",
		"content": strings.TrimSpace(systemInstruction),
	}}
	for _, item := range history {
		role := strings.ToLower(strings.TrimSpace(stringValue(item["role"])))
		if role == "model" {
			role = "assistant"
		} else if role != "user" {
			continue
		}

		parts := anyParts(item["parts"])
		text, _ := ExtractFreePoolTextParts(parts)
		if text == "" {
			continue
		}
		messages = append(messages, map[string]string{
			"role":    role,
			"content": text,
		})
	}
	messages = append(messages, map[string]string{
		"role":    "user",
		"content": currentText,
	})
	return messages
}

func ExtractFreePoolResponseText(payload map[string]any) string {
	choices, ok := payload["choices"].([]any)
	if !ok || len(choices) == 0 {
		return ""
	}
	choice, ok := choices[0].(map[string]any)
	if !ok {
		return ""
	}
	message, ok := choice["message"].(map[string]any)
	if !ok {
		return ""
	}

	switch content := message["content"].(type) {
	case string:
		return strings.TrimSpace(content)
	case []any:
		chunks := make([]string, 0, len(content))
		for _, item := range content {
			switch value := item.(type) {
			case string:
				chunks = append(chunks, value)
			case map[string]any:
				if text, ok := value["text"].(string); ok {
					chunks = append(chunks, text)
				}
			}
		}
		return strings.TrimSpace(strings.Join(chunks, "\n"))
	default:
		return ""
	}
}

func anyParts(value any) []map[string]any {
	if direct, ok := value.([]map[string]any); ok {
		return direct
	}
	items, ok := value.([]any)
	if !ok {
		return nil
	}
	parts := make([]map[string]any, 0, len(items))
	for _, item := range items {
		if part, ok := item.(map[string]any); ok {
			parts = append(parts, part)
		}
	}
	return parts
}

func stringValue(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	return ""
}
