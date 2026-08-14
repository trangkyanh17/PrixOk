package runtimecfg

import "strings"

func lastRuneIndex(values []rune, target rune, end int) int {
	if end > len(values) {
		end = len(values)
	}
	for index := end - 1; index >= 0; index-- {
		if values[index] == target {
			return index
		}
	}
	return -1
}

func SplitReplyChunks(cleanedText string) []string {
	remaining := strings.TrimSpace(cleanedText)
	chunks := []string{}

	for remaining != "" {
		runes := []rune(remaining)
		var chunk string
		if len(runes) <= 4000 {
			chunk = remaining
			remaining = ""
		} else {
			cut := lastRuneIndex(runes, '\n', 4000)
			if cut < 1000 {
				cut = lastRuneIndex(runes, ' ', 4000)
			}
			if cut < 1000 {
				cut = 4000
			}
			chunk = strings.TrimRight(string(runes[:cut]), " \t\r\n")
			remaining = strings.TrimLeft(string(runes[cut:]), " \t\r\n")
		}
		if chunk != "" {
			chunks = append(chunks, chunk)
		}
	}
	return chunks
}
