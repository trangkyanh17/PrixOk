package runtimecfg

import "strings"

type VertexPayloadOptions struct {
	SystemInstruction    string
	Contents             []any
	GenerationConfig     map[string]any
	ToolDeclarations     []map[string]any
	FunctionCallingMode  string
	AllowedFunctionNames []string
}

func cloneAnySlice(input []any) []any {
	if input == nil {
		return nil
	}
	output := make([]any, len(input))
	for index, value := range input {
		switch typed := value.(type) {
		case map[string]any:
			output[index] = cloneAnyMap(typed)
		case []any:
			output[index] = cloneAnySlice(typed)
		default:
			output[index] = value
		}
	}
	return output
}

func cloneStringSliceAsAny(input []string) []any {
	output := make([]any, 0, len(input))
	for _, value := range input {
		value = strings.TrimSpace(value)
		if value != "" {
			output = append(output, value)
		}
	}
	return output
}

func BuildVertexPayload(options VertexPayloadOptions) map[string]any {
	payload := map[string]any{
		"contents": cloneAnySlice(options.Contents),
	}

	if systemInstruction := strings.TrimSpace(options.SystemInstruction); systemInstruction != "" {
		payload["systemInstruction"] = map[string]any{
			"parts": []any{
				map[string]any{"text": systemInstruction},
			},
		}
	}

	if options.GenerationConfig != nil {
		payload["generationConfig"] = cloneAnyMap(options.GenerationConfig)
	}

	if len(options.ToolDeclarations) > 0 {
		declarations := make([]any, 0, len(options.ToolDeclarations))
		for _, declaration := range options.ToolDeclarations {
			if declaration == nil {
				continue
			}
			declarations = append(declarations, cloneAnyMap(declaration))
		}
		if len(declarations) > 0 {
			payload["tools"] = []any{
				map[string]any{
					"functionDeclarations": declarations,
				},
			}

			mode := strings.ToUpper(strings.TrimSpace(options.FunctionCallingMode))
			if mode == "" {
				mode = "AUTO"
			}
			functionCallingConfig := map[string]any{"mode": mode}
			if allowed := cloneStringSliceAsAny(options.AllowedFunctionNames); len(allowed) > 0 {
				functionCallingConfig["allowedFunctionNames"] = allowed
			}
			payload["toolConfig"] = map[string]any{
				"functionCallingConfig": functionCallingConfig,
			}
		}
	}

	return payload
}

func BuildRegistryVertexPayload(
	registry *ToolRegistry,
	mode string,
	allowPrivate bool,
	systemInstruction string,
	contents []any,
	generationConfig map[string]any,
) map[string]any {
	declarations := []map[string]any(nil)
	if registry != nil {
		declarations = registry.Declarations(mode, allowPrivate)
	}
	return BuildVertexPayload(VertexPayloadOptions{
		SystemInstruction:   systemInstruction,
		Contents:            contents,
		GenerationConfig:    generationConfig,
		ToolDeclarations:    declarations,
		FunctionCallingMode: "AUTO",
	})
}
