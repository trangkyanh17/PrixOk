package runtimecfg

import "testing"

func TestCleanPublicAnswerRemovesAutomaticResearchSection(t *testing.T) {
	input := "Câu trả lời chính.\n\n## Nguồn Google:\n- nguồn 1\n- nguồn 2"
	if got := CleanPublicAnswer(input); got != "Câu trả lời chính." {
		t.Fatalf("got=%q", got)
	}
}

func TestCleanPublicAnswerRemovesGroundingRedirectAndEmptyBullets(t *testing.T) {
	input := "Dòng 1\n- https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc\n-\n\n\n\nDòng 2"
	got := CleanPublicAnswer(input)
	want := "Dòng 1\n\nDòng 2"
	if got != want {
		t.Fatalf("got=%q want=%q", got, want)
	}
}

func TestCandidateFinishReason(t *testing.T) {
	payload := map[string]any{
		"candidates": []any{
			map[string]any{"finishReason": " max_tokens "},
		},
	}
	if got := CandidateFinishReason(payload); got != "MAX_TOKENS" {
		t.Fatalf("got=%q", got)
	}
	if got := CandidateFinishReason(map[string]any{}); got != "" {
		t.Fatalf("empty got=%q", got)
	}
}

func TestMergeResponseTextHandlesEmptySides(t *testing.T) {
	if got := MergeResponseText("", "  hello  "); got != "hello" {
		t.Fatalf("left empty=%q", got)
	}
	if got := MergeResponseText("hello  \n", ""); got != "hello" {
		t.Fatalf("right empty=%q", got)
	}
}

func TestMergeResponseTextDeduplicatesLongOverlapCaseInsensitive(t *testing.T) {
	current := "prefix ABCDEFGHIJKLMNOPQRSTUVWXYZ"
	continuation := "abcdefghijklmnopqrstuvwxyz suffix"
	got := MergeResponseText(current, continuation)
	want := "prefix ABCDEFGHIJKLMNOPQRSTUVWXYZ suffix"
	if got != want {
		t.Fatalf("got=%q want=%q", got, want)
	}
}

func TestMergeResponseTextAddsSpaceBetweenAlphanumericFragments(t *testing.T) {
	if got := MergeResponseText("hello", "world"); got != "hello world" {
		t.Fatalf("got=%q", got)
	}
	if got := MergeResponseText("xin", "chào"); got != "xin chào" {
		t.Fatalf("unicode got=%q", got)
	}
}

func TestMergeResponseTextDoesNotAddSpaceAroundPunctuation(t *testing.T) {
	if got := MergeResponseText("hello,", " world"); got != "hello,world" {
		t.Fatalf("got=%q", got)
	}
	if got := MergeResponseText("hello ", ".world"); got != "hello.world" {
		t.Fatalf("got=%q", got)
	}
}

func TestMergeResponseTextOverlapLimitIs600Runes(t *testing.T) {
	overlap := "abcdefghijklmnop"
	current := "start " + overlap
	continuation := overlap + " end"
	if got := MergeResponseText(current, continuation); got != "start "+overlap+" end" {
		t.Fatalf("got=%q", got)
	}
}
