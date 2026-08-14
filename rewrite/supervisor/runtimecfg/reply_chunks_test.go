package runtimecfg

import (
	"strings"
	"testing"
	"unicode/utf8"
)

func TestSplitReplyChunksShortText(t *testing.T) {
	got := SplitReplyChunks("  hello world  ")
	if len(got) != 1 || got[0] != "hello world" {
		t.Fatalf("got=%v", got)
	}
}

func TestSplitReplyChunksPrefersLateNewline(t *testing.T) {
	text := strings.Repeat("a", 2000) + "\n" + strings.Repeat("b", 2500)
	got := SplitReplyChunks(text)
	if len(got) != 2 {
		t.Fatalf("chunks=%d", len(got))
	}
	if utf8.RuneCountInString(got[0]) != 2000 || got[1] != strings.Repeat("b", 2500) {
		t.Fatalf("unexpected split lengths=%d,%d", utf8.RuneCountInString(got[0]), utf8.RuneCountInString(got[1]))
	}
}

func TestSplitReplyChunksFallsBackToSpace(t *testing.T) {
	text := strings.Repeat("a", 1800) + " " + strings.Repeat("b", 2500)
	got := SplitReplyChunks(text)
	if len(got) != 2 || utf8.RuneCountInString(got[0]) != 1800 {
		t.Fatalf("got lengths=%v", chunkRuneLengths(got))
	}
}

func TestSplitReplyChunksIgnoresEarlyBoundary(t *testing.T) {
	text := strings.Repeat("a", 500) + "\n" + strings.Repeat("b", 4100)
	got := SplitReplyChunks(text)
	if len(got) != 2 || utf8.RuneCountInString(got[0]) != 4000 {
		t.Fatalf("got lengths=%v", chunkRuneLengths(got))
	}
}

func TestSplitReplyChunksUsesRuneLimit(t *testing.T) {
	text := strings.Repeat("🙂", 4001)
	got := SplitReplyChunks(text)
	if len(got) != 2 || utf8.RuneCountInString(got[0]) != 4000 || got[1] != "🙂" {
		t.Fatalf("got lengths=%v", chunkRuneLengths(got))
	}
}

func TestSplitReplyChunksNeverReturnsBlankChunk(t *testing.T) {
	got := SplitReplyChunks(strings.Repeat("x", 3999) + " \n " + strings.Repeat("y", 10))
	for _, chunk := range got {
		if strings.TrimSpace(chunk) == "" {
			t.Fatalf("blank chunk in %v", got)
		}
	}
}

func chunkRuneLengths(chunks []string) []int {
	lengths := make([]int, 0, len(chunks))
	for _, chunk := range chunks {
		lengths = append(lengths, utf8.RuneCountInString(chunk))
	}
	return lengths
}
