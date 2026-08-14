package runtimecfg

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
)

const (
	DefaultDeltaForceNativeBinary = "/app/rewrite/target/release/atri-native"
	DefaultDeltaForceCNDBPath     = "/app/atri_data/delta_force_cn_s1_s10.sqlite3"
)

type DeltaForceNativeInvoker func(
	context.Context,
	string,
	string,
	string,
	[]byte,
) ([]byte, error)

type DeltaForceToolRuntime struct {
	BinaryPath string
	DBPath     string
	Invoker    DeltaForceNativeInvoker
}

func SearchDeltaForceCNDeclaration() map[string]any {
	return map[string]any{
		"name":        "search_delta_force_cn",
		"description": "Tra cứu knowledge base Delta Force bản Trung Quốc từ S1 đến S10. Bắt buộc dùng trước khi trả lời về vũ khí, đạn, giáp, bản đồ, operator, phương tiện, vật phẩm, mùa hoặc thay đổi cân bằng.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query": map[string]any{
					"type":        "string",
					"description": "Tên hoặc nội dung cần tra cứu.",
				},
				"season": map[string]any{
					"type":        "integer",
					"description": "Mùa CN từ 1 đến 10. Bỏ trống để dùng hiện hành.",
				},
				"category": map[string]any{
					"type":        "string",
					"description": "weapon, ammo, armor, helmet, map, operator, vehicle, attachment, gear, key, collectible, consumable, season hoặc balance.",
				},
				"mode": map[string]any{
					"type":        "string",
					"description": "operations hoặc warfare.",
				},
				"platform": map[string]any{
					"type":        "string",
					"description": "pc hoặc mobile.",
				},
				"limit": map[string]any{
					"type":    "integer",
					"minimum": 1,
					"maximum": 12,
				},
			},
			"required": []any{"query"},
		},
	}
}

func GetDeltaForceCNHistoryDeclaration() map[string]any {
	return map[string]any{
		"name":        "get_delta_force_cn_history",
		"description": "Tìm lịch sử một thực thể hoặc chủ đề trong tài liệu Delta Force China S1-S10. Dùng khi hỏi xuất hiện từ mùa nào, từng bị chỉnh ra sao hoặc lịch sử qua các mùa.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query": map[string]any{"type": "string"},
				"season_from": map[string]any{
					"type":    "integer",
					"minimum": 1,
					"maximum": 10,
				},
				"season_to": map[string]any{
					"type":    "integer",
					"minimum": 1,
					"maximum": 10,
				},
				"limit": map[string]any{
					"type":    "integer",
					"minimum": 1,
					"maximum": 20,
				},
			},
			"required": []any{"query"},
		},
	}
}

func CompareDeltaForceCNSeasonsDeclaration() map[string]any {
	return map[string]any{
		"name":        "compare_delta_force_cn_seasons",
		"description": "Lấy bằng chứng cùng một chủ đề ở hai mùa Delta Force China. Không tự suy ra thay đổi nếu nguồn không ghi rõ.",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query": map[string]any{"type": "string"},
				"season_a": map[string]any{
					"type":    "integer",
					"minimum": 1,
					"maximum": 10,
				},
				"season_b": map[string]any{
					"type":    "integer",
					"minimum": 1,
					"maximum": 10,
				},
				"limit": map[string]any{
					"type":    "integer",
					"minimum": 1,
					"maximum": 10,
				},
			},
			"required": []any{"query", "season_a", "season_b"},
		},
	}
}

func deltaForcePath(value string, fallback string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return fallback
	}
	return value
}

func (runtime DeltaForceToolRuntime) invoke(
	ctx context.Context,
	command string,
	arguments map[string]any,
) map[string]any {
	binaryPath := deltaForcePath(runtime.BinaryPath, DefaultDeltaForceNativeBinary)
	dbPath := deltaForcePath(runtime.DBPath, DefaultDeltaForceCNDBPath)
	requestJSON, err := json.Marshal(arguments)
	if err != nil {
		return map[string]any{
			"ok":     false,
			"error":  "Không mã hóa được Delta Force request: " + err.Error(),
			"region": "cn",
		}
	}

	invoker := runtime.Invoker
	if invoker == nil {
		invoker = func(
			ctx context.Context,
			binaryPath string,
			command string,
			dbPath string,
			requestJSON []byte,
		) ([]byte, error) {
			cmd := exec.CommandContext(
				ctx,
				binaryPath,
				command,
				dbPath,
				string(requestJSON),
			)
			return cmd.CombinedOutput()
		}
	}

	raw, err := invoker(ctx, binaryPath, command, dbPath, requestJSON)
	if err != nil {
		detail := strings.TrimSpace(string(raw))
		if detail == "" {
			detail = err.Error()
		}
		return map[string]any{
			"ok":     false,
			"error":  "Delta Force native runtime lỗi: " + truncateRunes(detail, 1000),
			"region": "cn",
		}
	}

	var result map[string]any
	if err := json.Unmarshal(raw, &result); err != nil {
		return map[string]any{
			"ok":     false,
			"error":  "Delta Force native runtime trả JSON không hợp lệ.",
			"region": "cn",
		}
	}
	if result == nil {
		return map[string]any{
			"ok":     false,
			"error":  "Delta Force native runtime trả kết quả rỗng.",
			"region": "cn",
		}
	}
	return result
}

func deltaForceOptionalInt(arguments map[string]any, name string) any {
	value, ok := arguments[name]
	if !ok || value == nil || googleString(value) == "" {
		return nil
	}
	parsed, ok := weatherInt(value)
	if !ok {
		return value
	}
	return parsed
}

func deltaForceSearchArguments(arguments map[string]any) map[string]any {
	request := map[string]any{
		"query":    googleString(arguments["query"]),
		"category": googleString(arguments["category"]),
		"mode":     googleString(arguments["mode"]),
		"platform": googleString(arguments["platform"]),
		"limit":    googleClampInt(arguments["limit"], 1, 12, 8),
	}
	if season := deltaForceOptionalInt(arguments, "season"); season != nil {
		request["season"] = season
	}
	return request
}

func deltaForceHistoryArguments(arguments map[string]any) map[string]any {
	return map[string]any{
		"query":       googleString(arguments["query"]),
		"season_from": googleClampInt(arguments["season_from"], 1, 10, 1),
		"season_to":   googleClampInt(arguments["season_to"], 1, 10, 10),
		"limit":       googleClampInt(arguments["limit"], 1, 20, 16),
	}
}

func deltaForceCompareArguments(arguments map[string]any) map[string]any {
	return map[string]any{
		"query":    googleString(arguments["query"]),
		"season_a": deltaForceOptionalInt(arguments, "season_a"),
		"season_b": deltaForceOptionalInt(arguments, "season_b"),
		"limit":    googleClampInt(arguments["limit"], 1, 10, 5),
	}
}

func (runtime DeltaForceToolRuntime) RegisteredTools() []RegisteredTool {
	return []RegisteredTool{
		{
			Name:        "search_delta_force_cn",
			Declaration: SearchDeltaForceCNDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.invoke(
					ctx,
					"delta-search",
					deltaForceSearchArguments(arguments),
				), nil
			},
		},
		{
			Name:        "get_delta_force_cn_history",
			Declaration: GetDeltaForceCNHistoryDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.invoke(
					ctx,
					"delta-history",
					deltaForceHistoryArguments(arguments),
				), nil
			},
		},
		{
			Name:        "compare_delta_force_cn_seasons",
			Declaration: CompareDeltaForceCNSeasonsDeclaration(),
			Privacy:     ToolPrivacyPublic,
			Executor: func(ctx context.Context, _ ToolContext, arguments map[string]any) (any, error) {
				return runtime.invoke(
					ctx,
					"delta-compare",
					deltaForceCompareArguments(arguments),
				), nil
			},
		},
	}
}

func RegisterDeltaForceTools(registry *ToolRegistry, runtime DeltaForceToolRuntime) error {
	if registry == nil {
		return fmt.Errorf("tool registry is nil")
	}
	for _, tool := range runtime.RegisteredTools() {
		if err := registry.Register(tool); err != nil {
			return err
		}
	}
	return nil
}
