---
name: deploy-doctor
description: "Diagnose and repair Atri deployment/runtime issues on Termux, Debian PRoot, tmux, VPS-like Linux environments, launchers, environment variables, and long-running services. Use for deployment, startup, restart, or process-health problems."
compatibility: "Designed for Atri's Linux/Termux/Debian PRoot deployment."
metadata:
  atri-privacy: "private"
  atri-worker-eligible: "false"
  atri-risk: "high"
  atri-model-hint: "vertex"
  atri-triggers: "deploy lỗi; deploy loi; termux lỗi; termux loi; debian proot; tmux bot; service không chạy; service khong chay; bot không start; bot khong start; launcher lỗi; launcher loi; vps deploy"
---

# Deploy Doctor

Repair deployment with the smallest reversible change.

## Workflow

1. Identify host layer, PRoot/container layer, process supervisor, launcher, and application process.
2. Verify paths, executable bits, environment variables, mounts/symlinks, and current process state.
3. Use fresh logs from the current restart window; do not diagnose from stale exceptions alone.
4. Back up changed production files before patching.
5. Compile/validate before restart when possible.
6. Restart only the required service/process.
7. Verify PID/process health plus an application-level startup marker.
8. Include rollback conditions for source-changing deployments.

## Atri environment guardrails

Do not print secrets. Do not change unrelated kernel/memory tuning. Do not make git commits or pushes unless explicitly requested.

## Output

Root cause, one repair path, validation, and rollback status.
