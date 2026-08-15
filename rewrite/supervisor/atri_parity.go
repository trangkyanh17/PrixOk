package main

import (
	"fmt"
	"regexp"
	"strings"
	"sync/atomic"
)

const atriParitySchemaVersion = 1

type atriParityEvent struct {
	Version          int               `json:"version"`
	Stage            string            `json:"stage"`
	RouteText        string            `json:"route_text,omitempty"`
	AttachmentRoute  string            `json:"attachment_route,omitempty"`
	ActualMode       string            `json:"actual_mode,omitempty"`
	ForceGitHubMCP   bool              `json:"force_github_mcp,omitempty"`
	Mode             string            `json:"mode,omitempty"`
	RuntimeModel     string            `json:"runtime_model,omitempty"`
	BaseModel        string            `json:"base_model,omitempty"`
	ResolvedModel    string            `json:"resolved_model,omitempty"`
	ThinkingAuto     bool              `json:"thinking_auto,omitempty"`
	ThinkingLevels   map[string]string `json:"thinking_levels,omitempty"`
	BaseThinking     string            `json:"base_thinking,omitempty"`
	ProviderModel    string            `json:"provider_model,omitempty"`
	ProviderThinking string            `json:"provider_thinking,omitempty"`
	ResolvedThinking string            `json:"resolved_thinking,omitempty"`
	ToolProfile      string            `json:"tool_profile,omitempty"`
	ToolName         string            `json:"tool_name,omitempty"`
}

type atriParityEngine struct {
	accepted   atomic.Uint64
	rejected   atomic.Uint64
	routeTotal atomic.Uint64
	routeMatch atomic.Uint64
	routeMiss  atomic.Uint64
	planTotal  atomic.Uint64
	planMatch  atomic.Uint64
	planMiss   atomic.Uint64
	toolTotal  atomic.Uint64
	toolMatch  atomic.Uint64
	toolMiss   atomic.Uint64
}

type atriParitySnapshot struct {
	Accepted   uint64 `json:"accepted"`
	Rejected   uint64 `json:"rejected"`
	RouteTotal uint64 `json:"route_total"`
	RouteMatch uint64 `json:"route_match"`
	RouteMiss  uint64 `json:"route_mismatch"`
	PlanTotal  uint64 `json:"plan_total"`
	PlanMatch  uint64 `json:"plan_match"`
	PlanMiss   uint64 `json:"plan_mismatch"`
	ToolTotal  uint64 `json:"tool_total"`
	ToolMatch  uint64 `json:"tool_match"`
	ToolMiss   uint64 `json:"tool_mismatch"`
}

func newAtriParityEngine() *atriParityEngine {
	return &atriParityEngine{}
}

func (engine *atriParityEngine) snapshot() atriParitySnapshot {
	if engine == nil {
		return atriParitySnapshot{}
	}
	return atriParitySnapshot{
		Accepted:   engine.accepted.Load(),
		Rejected:   engine.rejected.Load(),
		RouteTotal: engine.routeTotal.Load(),
		RouteMatch: engine.routeMatch.Load(),
		RouteMiss:  engine.routeMiss.Load(),
		PlanTotal:  engine.planTotal.Load(),
		PlanMatch:  engine.planMatch.Load(),
		PlanMiss:   engine.planMiss.Load(),
		ToolTotal:  engine.toolTotal.Load(),
		ToolMatch:  engine.toolMatch.Load(),
		ToolMiss:   engine.toolMiss.Load(),
	}
}

func validAtriMode(mode string) bool {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "chat", "web", "tools", "code":
		return true
	default:
		return false
	}
}

func expectedToolProfile(mode string) string {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "web":
		return "google_search"
	case "tools":
		return "tool_functions"
	case "code":
		return "code_plugins"
	default:
		return "none"
	}
}

var vietnameseFoldReplacer = strings.NewReplacer(
	"à", "a", "á", "a", "ạ", "a", "ả", "a", "ã", "a",
	"â", "a", "ầ", "a", "ấ", "a", "ậ", "a", "ẩ", "a", "ẫ", "a",
	"ă", "a", "ằ", "a", "ắ", "a", "ặ", "a", "ẳ", "a", "ẵ", "a",
	"è", "e", "é", "e", "ẹ", "e", "ẻ", "e", "ẽ", "e",
	"ê", "e", "ề", "e", "ế", "e", "ệ", "e", "ể", "e", "ễ", "e",
	"ì", "i", "í", "i", "ị", "i", "ỉ", "i", "ĩ", "i",
	"ò", "o", "ó", "o", "ọ", "o", "ỏ", "o", "õ", "o",
	"ô", "o", "ồ", "o", "ố", "o", "ộ", "o", "ổ", "o", "ỗ", "o",
	"ơ", "o", "ờ", "o", "ớ", "o", "ợ", "o", "ở", "o", "ỡ", "o",
	"ù", "u", "ú", "u", "ụ", "u", "ủ", "u", "ũ", "u",
	"ư", "u", "ừ", "u", "ứ", "u", "ự", "u", "ử", "u", "ữ", "u",
	"ỳ", "y", "ý", "y", "ỵ", "y", "ỷ", "y", "ỹ", "y",
	"đ", "d",
)

func foldAtriRouteText(text string) string {
	value := strings.ToLower(text)
	value = vietnameseFoldReplacer.Replace(value)
	return strings.Join(strings.Fields(value), " ")
}

var atriToolPatterns = []*regexp.Regexp{
	regexp.MustCompile(`\bthoi tiet\b`), regexp.MustCompile(`\bnhiet do\b`),
	regexp.MustCompile(`\bdu bao\b`), regexp.MustCompile(`\bweather\b`),
	regexp.MustCompile(`\bdelta force\b`), regexp.MustCompile(`三角洲行动`),
	regexp.MustCompile(`\byoutube\b`), regexp.MustCompile(`\btim video\b`),
	regexp.MustCompile(`\blink .*nguy hiem\b`), regexp.MustCompile(`\burl .*nguy hiem\b`),
	regexp.MustCompile(`\bphishing\b`), regexp.MustCompile(`\bmalware\b`),
	regexp.MustCompile(`\bsafe browsing\b`), regexp.MustCompile(`\bkiem tra link\b`),
	regexp.MustCompile(`\bgeocode\b`), regexp.MustCompile(`\btoa do\b`),
	regexp.MustCompile(`\bdia chi chuan\b`), regexp.MustCompile(`\bdich\b`),
	regexp.MustCompile(`\btranslate\b`), regexp.MustCompile(`\bgoogle books\b`),
	regexp.MustCompile(`\bocr\b`), regexp.MustCompile(`\bdoc chu trong anh\b`),
	regexp.MustCompile(`\btrich xuat chu\b`), regexp.MustCompile(`\bdocument ai\b`),
	regexp.MustCompile(`\bphan tich pdf\b`), regexp.MustCompile(`\bphan tich file\b`),
	regexp.MustCompile(`\bdoc file\b`), regexp.MustCompile(`\bdoc tai lieu\b`),
	regexp.MustCompile(`\bgoogle sheet\b`), regexp.MustCompile(`\bgoogle sheets\b`),
	regexp.MustCompile(`\bbang tinh\b`), regexp.MustCompile(`\bisbn\b`),
	regexp.MustCompile(`\btim sach\b`), regexp.MustCompile(`\bgmail\b`),
	regexp.MustCompile(`\bemail\b`), regexp.MustCompile(`\bmail\b`),
	regexp.MustCompile(`\bgoogle drive\b`), regexp.MustCompile(`\bdrive\b`),
	regexp.MustCompile(`\bcalendar\b`), regexp.MustCompile(`\blich cua tao\b`),
	regexp.MustCompile(`\blich hom nay\b`), regexp.MustCompile(`\blich ngay mai\b`),
	regexp.MustCompile(`\bnoi bang giong\b`), regexp.MustCompile(`\btra loi bang giong\b`),
	regexp.MustCompile(`\bdoc thanh tieng\b`), regexp.MustCompile(`\btts\b`),
	regexp.MustCompile(`\bgoogle tool\b`), regexp.MustCompile(`\bgoogle api nao\b`),
}

var atriWebPatterns = []*regexp.Regexp{
	regexp.MustCompile(`\btim tren mang\b`), regexp.MustCompile(`\btim tren web\b`),
	regexp.MustCompile(`\btim tren internet\b`), regexp.MustCompile(`\btra cuu\b`),
	regexp.MustCompile(`\bsearch web\b`), regexp.MustCompile(`\bgoogle search\b`),
	regexp.MustCompile(`\bnguon\b`), regexp.MustCompile(`\bsource\b`),
	regexp.MustCompile(`\bkiem chung\b`), regexp.MustCompile(`\bmoi nhat\b`),
	regexp.MustCompile(`\bhien tai\b`), regexp.MustCompile(`\blatest\b`),
	regexp.MustCompile(`\bcurrent\b`), regexp.MustCompile(`\brecent\b`),
	regexp.MustCompile(`\btin tuc\b`), regexp.MustCompile(`\bnews\b`),
	regexp.MustCompile(`\bversion\b`), regexp.MustCompile(`\bphien ban\b`),
	regexp.MustCompile(`\brelease\b`), regexp.MustCompile(`\bchangelog\b`),
	regexp.MustCompile(`\bcve[- ]?\d`), regexp.MustCompile(`\blo hong\b`),
	regexp.MustCompile(`\bdocs\b`), regexp.MustCompile(`\bdocumentation\b`),
	regexp.MustCompile(`\beol\b`), regexp.MustCompile(`\bend of life\b`),
	regexp.MustCompile(`\bho tro den\b`), regexp.MustCompile(`\bgia hien tai\b`),
	regexp.MustCompile(`\bprice\b`),
}

var atriURLPattern = regexp.MustCompile(`(?i)https?://[^\s]+`)

var githubLookupSignals = []string{
	"tim tren github", "tim github", "xem tren github", "xem github",
	"search github", "github search", "kiem tra github", "check github",
	"tra cuu github", "doc tren github",
}

func isExplicitGitHubLookupParity(text string) bool {
	value := foldAtriRouteText(text)
	if !strings.Contains(value, "github") {
		return false
	}
	for _, signal := range githubLookupSignals {
		if strings.Contains(value, signal) {
			return true
		}
	}
	return false
}

func chooseAtriModeOriginalParity(text string) string {
	normalized := foldAtriRouteText(text)
	if normalized == "" {
		return "chat"
	}
	for _, pattern := range atriToolPatterns {
		if pattern.MatchString(normalized) {
			return "tools"
		}
	}
	if atriURLPattern.MatchString(text) {
		return "web"
	}
	for _, pattern := range atriWebPatterns {
		if pattern.MatchString(normalized) {
			return "web"
		}
	}
	return "chat"
}

func chooseAtriModeParity(text string) string {
	value := strings.ToLower(strings.TrimSpace(text))
	githubContext := []string{
		"repo", "repository", "source", "code", "commit", "branch", "issue",
		"pull request", "pr ", "release", "github actions",
	}
	if isExplicitGitHubLookupParity(text) || (strings.Contains(value, "github") && containsAny(value, githubContext)) {
		return "code"
	}

	lifecycleValue := foldAtriRouteText(text)
	lifecycleSignals := []string{
		"eol", "end of life", "het ho tro", "ngung ho tro", "ho tro toi nam nao",
		"ho tro den nam nao", "ho tro toi khi nao", "ho tro den khi nao",
		"ho tro bao lau", "con duoc ho tro", "con ho tro khong", "maintenance until",
		"support until", "supported until", "security updates until", "end of support",
	}
	if containsAny(lifecycleValue, lifecycleSignals) {
		return "web"
	}

	codeSignals := []string{
		"context7", "serena", "semgrep", "sentry", "chrome devtools", "chrome-devtools",
		"github mcp", "code plugin", "code_plugin", "mcp tool", "mcp plugin",
		"viết code", "viet code", "sửa code", "sua code", "fix code", "debug",
		"traceback", "syntaxerror", "typeerror", "modulenotfounderror", "stack trace",
		"dockerfile", "docker compose", "docker-compose", "requirements.txt", "package.json",
		"pip install", "npm install", "git diff", "python", "javascript", "typescript",
		"golang", "rust", "c++", ".py", ".js", ".ts", ".go", ".rs", ".cpp",
		"tra docs", "tra tài liệu code",
	}
	if containsAny(value, codeSignals) {
		return "code"
	}

	directWorkspace := []string{
		"gmail", "google drive", "drive của tôi", "drive của tao", "drive của mình", "google calendar",
	}
	if containsAny(value, directWorkspace) {
		return "tools"
	}
	calendarWords := []string{"lịch", "calendar", "cuộc hẹn", "meeting", "appointment"}
	personalWords := []string{
		"của tôi", "của tao", "của mình", "hôm nay", "ngày mai", "7 ngày", "tuần này", "tuần tới", "sắp tới",
	}
	if containsAny(value, calendarWords) && containsAny(value, personalWords) {
		return "tools"
	}
	return chooseAtriModeOriginalParity(text)
}

func containsAny(value string, signals []string) bool {
	for _, signal := range signals {
		if strings.Contains(value, signal) {
			return true
		}
	}
	return false
}

func applyAttachmentRouteParity(mode, attachmentRoute string) string {
	mode = strings.ToLower(strings.TrimSpace(mode))
	attachmentRoute = strings.ToLower(strings.TrimSpace(attachmentRoute))
	if mode == "chat" && (attachmentRoute == "code" || attachmentRoute == "tools") {
		return attachmentRoute
	}
	return mode
}

func expectedBaseThinking(event atriParityEvent) (string, error) {
	mode := strings.ToLower(strings.TrimSpace(event.Mode))
	if !validAtriMode(mode) {
		return "", fmt.Errorf("invalid mode %q", event.Mode)
	}
	if event.ThinkingAuto {
		return map[string]string{
			"chat": "medium", "web": "high", "tools": "high", "code": "high",
		}[mode], nil
	}
	value := strings.ToLower(strings.TrimSpace(event.ThinkingLevels[mode]))
	if value == "" {
		return "", fmt.Errorf("missing manual thinking level for %s", mode)
	}
	return value, nil
}

func expectedResolvedModel(event atriParityEvent, base string) string {
	configured := strings.TrimSpace(event.ProviderModel)
	if configured == "" || strings.EqualFold(configured, "auto") {
		return base
	}
	return configured
}

func expectedResolvedThinking(event atriParityEvent, base string) string {
	configured := strings.ToLower(strings.TrimSpace(event.ProviderThinking))
	if configured == "" || configured == "auto" {
		return base
	}
	return configured
}

var codeToolNames = map[string]struct{}{
	"code_web_search": {}, "code_plugin_search": {}, "code_plugin_call": {},
	"code_plugin_batch": {}, "code_plugin_status": {}, "code_context7_docs": {},
}

// This is the exact tools-mode function surface currently exposed to Vertex.
// google_places_search/google_route have executors but are not in
// GOOGLE_TOOL_DECLARATIONS, so they intentionally remain invalid here.
var toolsModeNames = map[string]struct{}{
	"get_weather":                    {},
	"search_delta_force_cn":          {},
	"get_delta_force_cn_history":     {},
	"compare_delta_force_cn_seasons": {},
	"google_youtube_search":          {},
	"google_safe_browsing":           {},
	"google_translate":               {},
	"google_books_search":            {},
	"google_drive_search":            {},
	"google_drive_read_text":         {},
	"google_calendar_events":         {},
	"google_gmail_search":            {},
	"google_gmail_read":              {},
	"google_tts_speak":               {},
	"google_geocode":                 {},
	"google_vision_ocr":              {},
	"google_document_ai":             {},
	"google_sheets_read":             {},
	"google_capabilities":            {},
}

func validateObservedTool(mode, profile, name string) bool {
	mode = strings.ToLower(strings.TrimSpace(mode))
	profile = strings.ToLower(strings.TrimSpace(profile))
	name = strings.TrimSpace(name)
	if profile != expectedToolProfile(mode) || name == "" {
		return false
	}
	switch mode {
	case "code":
		_, ok := codeToolNames[name]
		return ok
	case "tools":
		_, ok := toolsModeNames[name]
		return ok
	default:
		return false
	}
}

func (engine *atriParityEngine) evaluate(event atriParityEvent) (bool, string, error) {
	if engine == nil {
		return false, "engine_nil", fmt.Errorf("parity engine is nil")
	}
	if event.Version != atriParitySchemaVersion {
		engine.rejected.Add(1)
		return false, "schema", fmt.Errorf("unsupported parity schema version %d", event.Version)
	}

	switch strings.ToLower(strings.TrimSpace(event.Stage)) {
	case "route":
		engine.routeTotal.Add(1)
		expectedMode := applyAttachmentRouteParity(chooseAtriModeParity(event.RouteText), event.AttachmentRoute)
		expectedGitHub := expectedMode == "code" && isExplicitGitHubLookupParity(event.RouteText)
		match := expectedMode == strings.ToLower(strings.TrimSpace(event.ActualMode)) && expectedGitHub == event.ForceGitHubMCP
		engine.accepted.Add(1)
		if match {
			engine.routeMatch.Add(1)
			return true, "ok", nil
		}
		engine.routeMiss.Add(1)
		if expectedMode != strings.ToLower(strings.TrimSpace(event.ActualMode)) {
			return false, "route_mode", nil
		}
		return false, "github_force", nil

	case "vertex_plan":
		engine.planTotal.Add(1)
		mode := strings.ToLower(strings.TrimSpace(event.Mode))
		if !validAtriMode(mode) || strings.TrimSpace(event.RuntimeModel) == "" {
			engine.rejected.Add(1)
			return false, "plan_input", fmt.Errorf("invalid vertex plan input")
		}
		expectedBaseModel := strings.TrimSpace(event.RuntimeModel)
		if mode == "code" {
			expectedBaseModel = "gemini-3.6-flash"
		}
		expectedThinking, err := expectedBaseThinking(event)
		if err != nil {
			engine.rejected.Add(1)
			return false, "thinking_input", err
		}
		expectedModel := expectedResolvedModel(event, expectedBaseModel)
		expectedResolvedThink := expectedResolvedThinking(event, expectedThinking)
		expectedProfile := expectedToolProfile(mode)
		match := expectedBaseModel == strings.TrimSpace(event.BaseModel) &&
			expectedModel == strings.TrimSpace(event.ResolvedModel) &&
			expectedThinking == strings.ToLower(strings.TrimSpace(event.BaseThinking)) &&
			expectedResolvedThink == strings.ToLower(strings.TrimSpace(event.ResolvedThinking)) &&
			expectedProfile == strings.ToLower(strings.TrimSpace(event.ToolProfile))
		engine.accepted.Add(1)
		if match {
			engine.planMatch.Add(1)
			return true, "ok", nil
		}
		engine.planMiss.Add(1)
		switch {
		case expectedBaseModel != strings.TrimSpace(event.BaseModel):
			return false, "base_model", nil
		case expectedModel != strings.TrimSpace(event.ResolvedModel):
			return false, "resolved_model", nil
		case expectedThinking != strings.ToLower(strings.TrimSpace(event.BaseThinking)):
			return false, "base_thinking", nil
		case expectedResolvedThink != strings.ToLower(strings.TrimSpace(event.ResolvedThinking)):
			return false, "resolved_thinking", nil
		default:
			return false, "tool_profile", nil
		}

	case "tool":
		engine.toolTotal.Add(1)
		match := validateObservedTool(event.Mode, event.ToolProfile, event.ToolName)
		engine.accepted.Add(1)
		if match {
			engine.toolMatch.Add(1)
			return true, "ok", nil
		}
		engine.toolMiss.Add(1)
		return false, "tool_boundary", nil
	default:
		engine.rejected.Add(1)
		return false, "stage", fmt.Errorf("unsupported parity stage %q", event.Stage)
	}
}
