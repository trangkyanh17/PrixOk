---
name: log-diagnoser
description: "Analyze runtime logs, stack traces, crash output, service logs, and timelines to isolate the real failure. Use when the user provides logs or asks what a traceback, crash, journal, or runtime error means."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "medium"
  atri-model-hint: "vertex"
  atri-triggers: "check log; đọc log; doc log; phân tích log; phan tich log; traceback; stack trace; crash log; journalctl; runtime log; bắt bệnh log; bat benh log"
---

# Log Diagnoser

Treat logs as a timeline, not as a bag of error strings.

## Workflow

1. Identify the time window and the process/component producing each line.
2. Separate startup noise, warnings, expected test failures, and the first real production failure.
3. Find the earliest causal error, then follow downstream consequences.
4. Correlate status codes, retries, restarts, process IDs, and state transitions.
5. Distinguish synthetic smoke-test exceptions from real runtime faults.
6. Do not treat an old traceback as a current failure unless the timeline supports it.
7. Recommend one focused validation that can confirm or reject the diagnosis.

## Security

Never repeat tokens, credentials, cookies, private URLs, or other secrets found in logs. Summarize them as redacted secret material.

## Output

Use: root cause → evidence → current health → next action.
