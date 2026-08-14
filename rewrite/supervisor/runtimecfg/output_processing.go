package runtimecfg

import (
	"regexp"
	"strings"
	"unicode"
)

const groundingRedirectFragment = "vertexaisearch.cloud.google.com/grounding-api-redirect"

var researchSourceHeadingRE = regexp.MustCompile(`(?im)^[ \t]*(?:#{1,6}[ \t]*)?(?:Nguồn Google|Truy vấn Google|Google Grounding Sources?)[ \t]*:[ \t]*$`)
var emptyBulletLineRE = regexp.MustCompile(`(?m)^[ \t]*[-•*][ \t]*$`)
var excessiveBlankLinesRE = regexp.MustCompile(`\n{3,}`)

func CleanPublicAnswer(text string) string {
	value := strings.TrimSpace(text)
	if value == "" {
		return ""
	}

	if match := researchSourceHeadingRE.FindStringIndex(value); match != nil {
		value = strings.TrimRight(value[:match[0]], " \t\r\n")
	}

	lines := strings.Split(value, "\n")
	cleanLines := make([]string, 0, len(lines))
	for _, line := range lines {
		if strings.Contains(strings.ToLower(line), groundingRedirectFragment) {
			continue
		}
		cleanLines = append(cleanLines, line)
	}

	value = strings.TrimSpace(strings.Join(cleanLines, "\n"))
	value = emptyBulletLineRE.ReplaceAllString(value, "")
	value = excessiveBlankLinesRE.ReplaceAllString(value, "\n\n")
	return strings.TrimSpace(value)
}

func CandidateFinishReason(payload map[string]any) string {
	candidates, ok := payload["candidates"].([]any)
	if !ok || len(candidates) == 0 {
		return ""
	}
	candidate, ok := candidates[0].(map[string]any)
	if !ok {
		return ""
	}
	return strings.ToUpper(strings.TrimSpace(stringField(candidate["finishReason"])))
}

func runeSlicesEqualFold(left, right []rune) bool {
	return strings.EqualFold(string(left), string(right))
}

func isAlphaNumericRune(value rune) bool {
	return unicode.IsLetter(value) || unicode.IsNumber(value)
}

func MergeResponseText(current, continuation string) string {
	if current == "" {
		return strings.TrimSpace(continuation)
	}
	if continuation == "" {
		return strings.TrimRight(current, " \t\r\n")
	}

	left := strings.TrimRight(current, " \t\r\n")
	right := strings.TrimLeft(continuation, " \t\r\n")
	leftRunes := []rune(left)
	rightRunes := []rune(right)
	maxOverlap := len(leftRunes)
	if len(rightRunes) < maxOverlap {
		maxOverlap = len(rightRunes)
	}
	if maxOverlap > 600 {
		maxOverlap = 600
	}
	for overlap := maxOverlap; overlap > 15; overlap-- {
		if runeSlicesEqualFold(leftRunes[len(leftRunes)-overlap:], rightRunes[:overlap]) {
			return left + string(rightRunes[overlap:])
		}
	}

	if len(leftRunes) > 0 && len(rightRunes) > 0 && isAlphaNumericRune(leftRunes[len(leftRunes)-1]) && isAlphaNumericRune(rightRunes[0]) {
		return left + " " + right
	}
	return left + right
}
