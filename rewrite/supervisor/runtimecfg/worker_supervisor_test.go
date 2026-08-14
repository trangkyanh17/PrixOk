package runtimecfg

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestWorkerSupervisorRoutesChatDirect(t *testing.T) {
	result := RunWorkerSupervisor(
		context.Background(),
		"hello there",
		WorkerSupervisorOptions{Mode: "chat"},
		nil,
		nil,
	)
	if result.UseWorker || result.DirectReason != "chat_vertex_first" || result.TaskType != "chat" {
		t.Fatalf("result=%+v", result)
	}
}

func TestWorkerSupervisorHonorsPrivacyAndPrivateSkillRoutes(t *testing.T) {
	private := RunWorkerSupervisor(
		context.Background(),
		"debug repo của tôi",
		WorkerSupervisorOptions{Mode: "chat"},
		nil,
		nil,
	)
	if private.UseWorker || private.DirectReason != "private_phrase" {
		t.Fatalf("private=%+v", private)
	}

	skill := RunWorkerSupervisor(
		context.Background(),
		"debug python function này",
		WorkerSupervisorOptions{Mode: "chat", SkillPrivateRoute: true},
		nil,
		nil,
	)
	if skill.UseWorker || skill.DirectReason != "skill_private_or_vertex_only" {
		t.Fatalf("skill=%+v", skill)
	}
}

func TestWorkerSupervisorRunsWorkerVerifierAndFinalContext(t *testing.T) {
	workerCalls := 0
	worker := func(ctx context.Context, taskType, systemInstruction, prompt string) (*FreeReply, error) {
		workerCalls++
		if taskType != "coding" || !strings.Contains(systemInstruction, "worker nội bộ") || prompt != "debug python function này" {
			t.Fatalf("task=%q system=%q prompt=%q", taskType, systemInstruction, prompt)
		}
		return &FreeReply{Text: "draft one", Provider: "groq", Model: "qwen"}, nil
	}
	verifierCalls := 0
	verifier := func(ctx context.Context, prompt string) (string, error) {
		verifierCalls++
		if !strings.Contains(prompt, "draft one") {
			t.Fatalf("verify prompt=%q", prompt)
		}
		return `{"verdict":"PASS","feedback":"good"}`, nil
	}

	result := RunWorkerSupervisor(
		context.Background(),
		"debug python function này",
		WorkerSupervisorOptions{Mode: "chat", BaseMemoryContext: "BASE"},
		worker,
		verifier,
	)
	if !result.UseWorker || result.TaskType != "coding" || result.VerifierVerdict != "PASS" || result.Retried {
		t.Fatalf("result=%+v", result)
	}
	if workerCalls != 1 || verifierCalls != 1 {
		t.Fatalf("worker=%d verifier=%d", workerCalls, verifierCalls)
	}
	if !strings.Contains(result.SupervisorContext, "BASE") || !strings.Contains(result.SupervisorContext, "draft one") || !strings.Contains(result.SupervisorContext, "verdict=PASS") {
		t.Fatalf("context=%q", result.SupervisorContext)
	}
}

func TestWorkerSupervisorRetriesOnceAndReverifies(t *testing.T) {
	workerCalls := 0
	worker := func(ctx context.Context, taskType, systemInstruction, prompt string) (*FreeReply, error) {
		workerCalls++
		if workerCalls == 1 {
			return &FreeReply{Text: "draft bad", Provider: "groq", Model: "qwen"}, nil
		}
		if !strings.Contains(prompt, "ATRI WORKER RETRY V25.1") || !strings.Contains(prompt, "fix bug") {
			t.Fatalf("retry prompt=%q", prompt)
		}
		return &FreeReply{Text: "draft fixed", Provider: "cerebras", Model: "oss"}, nil
	}
	verifierCalls := 0
	verifier := func(ctx context.Context, prompt string) (string, error) {
		verifierCalls++
		if verifierCalls == 1 {
			return `{"verdict":"RETRY","feedback":"fix bug"}`, nil
		}
		if !strings.Contains(prompt, "draft fixed") {
			t.Fatalf("second verify=%q", prompt)
		}
		return `{"verdict":"PASS","feedback":"fixed"}`, nil
	}

	result := RunWorkerSupervisor(
		context.Background(),
		"debug python function này",
		WorkerSupervisorOptions{Mode: "chat"},
		worker,
		verifier,
	)
	if !result.UseWorker || !result.Retried || result.VerifierVerdict != "PASS" {
		t.Fatalf("result=%+v", result)
	}
	if result.WorkerText != "draft fixed" || result.WorkerProvider != "cerebras" || result.WorkerModel != "oss" {
		t.Fatalf("worker result=%+v", result)
	}
	if workerCalls != 2 || verifierCalls != 2 {
		t.Fatalf("worker=%d verifier=%d", workerCalls, verifierCalls)
	}
}

func TestWorkerSupervisorKeepsFirstDraftWhenRetryFails(t *testing.T) {
	workerCalls := 0
	worker := func(ctx context.Context, taskType, systemInstruction, prompt string) (*FreeReply, error) {
		workerCalls++
		if workerCalls == 1 {
			return &FreeReply{Text: "draft one", Provider: "groq", Model: "qwen"}, nil
		}
		return nil, errors.New("retry failed")
	}
	verifier := func(ctx context.Context, prompt string) (string, error) {
		return `{"verdict":"RETRY","feedback":"needs fix"}`, nil
	}

	result := RunWorkerSupervisor(
		context.Background(),
		"debug python function này",
		WorkerSupervisorOptions{Mode: "chat"},
		worker,
		verifier,
	)
	if !result.UseWorker || !result.Retried || result.WorkerText != "draft one" || result.VerifierVerdict != "RETRY" {
		t.Fatalf("result=%+v", result)
	}
}

func TestWorkerSupervisorVerificationFailureFinalizesUnknown(t *testing.T) {
	worker := func(ctx context.Context, taskType, systemInstruction, prompt string) (*FreeReply, error) {
		return &FreeReply{Text: "draft", Provider: "groq", Model: "qwen"}, nil
	}
	verifier := func(ctx context.Context, prompt string) (string, error) {
		return "", errors.New("vertex unavailable")
	}
	result := RunWorkerSupervisor(
		context.Background(),
		"nghiên cứu benchmark này",
		WorkerSupervisorOptions{Mode: "chat"},
		worker,
		verifier,
	)
	if !result.UseWorker || result.VerifierVerdict != "UNKNOWN" || result.VerifierFeedback != "" {
		t.Fatalf("result=%+v", result)
	}
}

func TestWorkerSupervisorCodingHintPromotesPublicChat(t *testing.T) {
	worker := func(ctx context.Context, taskType, systemInstruction, prompt string) (*FreeReply, error) {
		if taskType != "coding" {
			t.Fatalf("task=%q", taskType)
		}
		return &FreeReply{Text: "draft", Provider: "groq", Model: "qwen"}, nil
	}
	result := RunWorkerSupervisor(
		context.Background(),
		"hãy làm phần này",
		WorkerSupervisorOptions{Mode: "chat", CodingSkillHint: true},
		worker,
		nil,
	)
	if !result.UseWorker || result.TaskType != "coding" {
		t.Fatalf("result=%+v", result)
	}
}
