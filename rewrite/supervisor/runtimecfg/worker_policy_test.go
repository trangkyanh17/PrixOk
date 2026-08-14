package runtimecfg

import (
	"strings"
	"testing"
	"unicode/utf8"
)

func TestClassifyFreeTask(t *testing.T) {
	cases := []struct {
		text string
		want string
	}{
		{"hello there", "chat"},
		{"debug python function này", "coding"},
		{"refactor toàn codebase này", "coding_agentic"},
		{"nghiên cứu benchmark này", "research"},
		{"tổng hợp nhiều tài liệu", "research_long"},
	}
	for _, test := range cases {
		if got := ClassifyFreeTask(test.text); got != test.want {
			t.Fatalf("text=%q got=%q want=%q", test.text, got, test.want)
		}
	}

	longCoding := "python " + strings.Repeat("x", 2500)
	if got := ClassifyFreeTask(longCoding); got != "coding_agentic" {
		t.Fatalf("long coding=%q", got)
	}
	longResearch := "research " + strings.Repeat("x", 6000)
	if got := ClassifyFreeTask(longResearch); got != "research_long" {
		t.Fatalf("long research=%q", got)
	}
}

func TestIsWorkerTask(t *testing.T) {
	for _, task := range []string{"coding", "coding_agentic", "research", "research_long"} {
		if !IsWorkerTask(task) {
			t.Fatalf("expected worker task: %s", task)
		}
	}
	if IsWorkerTask("chat") {
		t.Fatal("chat must not be a worker task")
	}
}

func TestWorkerSystemInstructionVariesByTask(t *testing.T) {
	coding := WorkerSystemInstruction("coding")
	research := WorkerSystemInstruction("research")
	if !strings.Contains(coding, "Ưu tiên code đúng") {
		t.Fatalf("coding=%q", coding)
	}
	if !strings.Contains(research, "tách fact khỏi suy luận") {
		t.Fatalf("research=%q", research)
	}
	if !strings.Contains(coding, "worker nội bộ") || !strings.Contains(research, "worker nội bộ") {
		t.Fatal("common worker guard missing")
	}
}

func TestSupervisorWorkerContextBoundsDraft(t *testing.T) {
	context := SupervisorWorkerContext("coding", "groq", "model-x", strings.Repeat("a", 24_001))
	if !strings.Contains(context, "[WORKER_OUTPUT_TRUNCATED]") {
		t.Fatal("missing truncation marker")
	}
	if !strings.Contains(context, "task_type=coding") || !strings.Contains(context, "worker_provider=groq") {
		t.Fatalf("context=%q", context)
	}
}

func TestWorkerVerificationPromptContainsPublicPayload(t *testing.T) {
	prompt := WorkerVerificationPrompt("CODING", "hãy sửa lỗi", "draft")
	if !strings.Contains(prompt, `"task_type":"coding"`) {
		t.Fatalf("prompt=%q", prompt)
	}
	if !strings.Contains(prompt, `"public_user_request":"hãy sửa lỗi"`) {
		t.Fatalf("prompt=%q", prompt)
	}
	if !strings.Contains(prompt, "ONLY public-task material") {
		t.Fatal("public-only guard missing")
	}
}

func TestParseWorkerVerdictHandlesJSONFenceAndFallback(t *testing.T) {
	verdict, feedback := ParseWorkerVerdict(`{"verdict":"PASS","feedback":"ok"}`)
	if verdict != "PASS" || feedback != "ok" {
		t.Fatalf("plain verdict=%q feedback=%q", verdict, feedback)
	}

	verdict, feedback = ParseWorkerVerdict("```json\n{\"verdict\":\"RETRY\",\"feedback\":\"fix edge case\"}\n```")
	if verdict != "RETRY" || feedback != "fix edge case" {
		t.Fatalf("fenced verdict=%q feedback=%q", verdict, feedback)
	}

	verdict, _ = ParseWorkerVerdict("VERDICT: PASS")
	if verdict != "PASS" {
		t.Fatalf("fallback verdict=%q", verdict)
	}
}

func TestParseWorkerVerdictBoundsFeedbackByRunes(t *testing.T) {
	feedback := strings.Repeat("🙂", 4001)
	input := `{"verdict":"RETRY","feedback":"` + feedback + `"}`
	verdict, got := ParseWorkerVerdict(input)
	if verdict != "RETRY" {
		t.Fatalf("verdict=%q", verdict)
	}
	if utf8.RuneCountInString(got) != 4003 || !strings.HasSuffix(got, "...") {
		t.Fatalf("runes=%d suffix=%v", utf8.RuneCountInString(got), strings.HasSuffix(got, "..."))
	}
}

func TestWorkerRetryAndVerificationContext(t *testing.T) {
	retry := WorkerRetryPrompt("research", "request", "draft", "feedback")
	if !strings.Contains(retry, `"verifier_feedback":"feedback"`) {
		t.Fatalf("retry=%q", retry)
	}
	verification := SupervisorVerificationContext("bad", "note", true)
	if !strings.Contains(verification, "verdict=UNKNOWN") || !strings.Contains(verification, "worker_retried=yes") {
		t.Fatalf("verification=%q", verification)
	}
}
