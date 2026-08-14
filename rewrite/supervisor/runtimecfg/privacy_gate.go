package runtimecfg

import (
	"regexp"
	"strings"
	"unicode/utf8"
)

var privateTaskPhrases = []string{
	"production source",
	"source production",
	"mã nguồn production",
	"code production",
	"repo của tôi",
	"project của tôi",
	"repository của tôi",
	"private repo",
	"private repository",
	"confidential",
	"bí mật nội bộ",
	"dữ liệu riêng tư",
	"thông tin riêng tư",
	"gmail của tôi",
	"email của tôi",
	"lịch của tôi",
	"calendar của tôi",
	"drive của tôi",
	"google drive của tôi",
	"tài khoản của tôi",
	"my gmail",
	"my email",
	"my calendar",
	"my drive",
	"my account",
	"bộ nhớ của tôi",
	"memory của tôi",
}

var privateTaskPaths = []string{
	"/app/",
	"/home/prix/",
	"/data/adb/",
	"vertex-service-account.json",
	"free-providers.env",
	"/secrets/",
}

var publicLongTextMarkers = []string{
	"public",
	"công khai",
	"open source",
	"wikipedia",
	"documentation",
	"tài liệu công khai",
	"http://",
	"https://",
}

var secretTaskPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\b(?:api[_ -]?key|token|secret|password|passwd)\s*[:=]\s*\S+`),
	regexp.MustCompile(`(?i)\b[A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*\S+`),
	regexp.MustCompile(`(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}`),
	regexp.MustCompile(`\bsk-[A-Za-z0-9_-]{16,}\b`),
	regexp.MustCompile(`\bgh[pousr]_[A-Za-z0-9_]{16,}\b`),
	regexp.MustCompile(`\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b`),
}

var pastedSourcePattern = regexp.MustCompile(`(?im)^\s*(?:def|class|from|import)\s+[A-Za-z_]`)

func PublicTaskPrivacyGate(text, mode string) (bool, string) {
	raw := text
	folded := strings.ToLower(raw)

	if strings.ToLower(strings.TrimSpace(mode)) != "chat" {
		return false, "mode_not_chat"
	}
	if strings.TrimSpace(raw) == "" {
		return false, "empty"
	}
	if strings.HasPrefix(strings.TrimLeft(raw, " \t\r\n"), "/") {
		return false, "command"
	}
	if containsAnyFolded(folded, privateTaskPhrases) {
		return false, "private_phrase"
	}
	if containsAnyFolded(folded, privateTaskPaths) {
		return false, "private_path"
	}
	if strings.Contains(folded, "-----begin ") && strings.Contains(folded, "private key-----") {
		return false, "private_key"
	}
	for _, pattern := range secretTaskPatterns {
		if pattern.FindStringIndex(raw) != nil {
			return false, "secret_pattern"
		}
	}
	if strings.Contains(raw, "```") {
		return false, "pasted_code"
	}
	if strings.Contains(raw, "\n") && pastedSourcePattern.FindStringIndex(raw) != nil {
		return false, "pasted_source"
	}
	if strings.Contains(folded, "traceback (most recent call last)") {
		return false, "pasted_traceback"
	}
	if utf8.RuneCountInString(raw) > 6000 && !containsAnyFolded(folded, publicLongTextMarkers) {
		return false, "long_text_unknown_privacy"
	}
	return true, "public_safe"
}
