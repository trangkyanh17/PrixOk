package runtimecfg

import (
	"context"
	"strings"
)

type WorkerGenerateFunc func(
	ctx context.Context,
	taskType string,
	systemInstruction string,
	prompt string,
) (*FreeReply, error)

type WorkerVerifyFunc func(ctx context.Context, prompt string) (string, error)

type WorkerSupervisorOptions struct {
	Mode                   string
	BaseMemoryContext      string
	WorkerSkillInstruction string
	SkillPrivateRoute      bool
	CodingSkillHint        bool
}

type WorkerSupervisorResult struct {
	UseWorker         bool
	DirectReason      string
	TaskType          string
	WorkerText        string
	WorkerProvider    string
	WorkerModel       string
	VerifierVerdict   string
	VerifierFeedback  string
	Retried           bool
	SupervisorContext string
}

func directWorkerResult(reason, taskType string) WorkerSupervisorResult {
	return WorkerSupervisorResult{
		UseWorker:    false,
		DirectReason: reason,
		TaskType:     taskType,
	}
}

func RunWorkerSupervisor(
	ctx context.Context,
	publicRawText string,
	options WorkerSupervisorOptions,
	generateWorker WorkerGenerateFunc,
	verifyWorker WorkerVerifyFunc,
) WorkerSupervisorResult {
	mode := strings.ToLower(strings.TrimSpace(options.Mode))
	if mode == "" {
		mode = "chat"
	}
	allowed, privacyReason := PublicTaskPrivacyGate(publicRawText, mode)
	if !allowed {
		return directWorkerResult(privacyReason, "")
	}
	if options.SkillPrivateRoute {
		return directWorkerResult("skill_private_or_vertex_only", "")
	}

	taskType := ClassifyFreeTask(publicRawText)
	if !IsWorkerTask(taskType) && options.CodingSkillHint {
		taskType = "coding"
	}
	if !IsWorkerTask(taskType) {
		return directWorkerResult("chat_vertex_first", taskType)
	}
	if generateWorker == nil {
		return directWorkerResult("worker_no_result", taskType)
	}

	systemInstruction := WorkerSystemInstruction(taskType) + options.WorkerSkillInstruction
	workerReply, err := generateWorker(ctx, taskType, systemInstruction, strings.TrimSpace(publicRawText))
	if err != nil || workerReply == nil || strings.TrimSpace(workerReply.Text) == "" {
		return directWorkerResult("worker_no_result", taskType)
	}

	workerText := strings.TrimSpace(workerReply.Text)
	workerProvider := strings.TrimSpace(workerReply.Provider)
	workerModel := strings.TrimSpace(workerReply.Model)
	verdict := "UNKNOWN"
	feedback := ""
	retried := false

	if verifyWorker != nil {
		verifyPrompt := WorkerVerificationPrompt(taskType, publicRawText, workerText)
		if verifyText, verifyErr := verifyWorker(ctx, verifyPrompt); verifyErr == nil {
			verdict, feedback = ParseWorkerVerdict(verifyText)
		}
	}

	if verdict == "RETRY" {
		retried = true
		retryPrompt := WorkerRetryPrompt(taskType, publicRawText, workerText, feedback)
		if retryReply, retryErr := generateWorker(ctx, taskType, systemInstruction, retryPrompt); retryErr == nil && retryReply != nil && strings.TrimSpace(retryReply.Text) != "" {
			workerText = strings.TrimSpace(retryReply.Text)
			workerProvider = strings.TrimSpace(retryReply.Provider)
			workerModel = strings.TrimSpace(retryReply.Model)

			if verifyWorker != nil {
				verifyPrompt := WorkerVerificationPrompt(taskType, publicRawText, workerText)
				if verifyText, verifyErr := verifyWorker(ctx, verifyPrompt); verifyErr == nil {
					verdict, feedback = ParseWorkerVerdict(verifyText)
				} else {
					verdict = "UNKNOWN"
					feedback = ""
				}
			}
		}
	}

	supervisorContext := strings.TrimSpace(options.BaseMemoryContext) +
		SupervisorWorkerContext(taskType, workerProvider, workerModel, workerText) +
		SupervisorVerificationContext(verdict, feedback, retried)

	return WorkerSupervisorResult{
		UseWorker:         true,
		TaskType:          taskType,
		WorkerText:        workerText,
		WorkerProvider:    workerProvider,
		WorkerModel:       workerModel,
		VerifierVerdict:   verdict,
		VerifierFeedback:  feedback,
		Retried:           retried,
		SupervisorContext: supervisorContext,
	}
}
