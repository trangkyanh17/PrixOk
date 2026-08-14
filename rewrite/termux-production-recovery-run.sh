#!/usr/bin/env bash
# Sourced by termux-production-recovery-host.sh after bot recovery.

if [[ "${RESULTS[BOT_LOCK]:-FAIL}" == PASS && "${RESULTS[PROD_HEALTH]:-FAIL}" == PASS ]]; then
  section "MCP COEXISTENCE"
  if start_mcp && wait_for_log_pattern "$MCP_LOG" 'MCP lifecycle startup:' "$MCP_STARTUP_TIMEOUT"; then
    startup_line="$(grep 'MCP lifecycle startup:' "$MCP_LOG" | tail -1)"
    if ready_line_ok "$startup_line"; then
      RSS_BEFORE="$(sum_tree_rss_kb "$MCP_PID")"
      sleep "$MCP_SOAK_SECONDS"
      health_line="$(grep 'MCP lifecycle health:' "$MCP_LOG" | tail -1 || true)"
      if [[ -n "$health_line" ]] && ready_line_ok "$health_line" && \
         [[ "$(bot_session_state)" == PRESENT ]] && \
         [[ "$(bot_lock_state || echo UNKNOWN)" == HELD ]] && \
         [[ "$(local_health_state)" == HEALTHY ]]; then
        pass MCP_COEXIST "$health_line"
      else
        fail MCP_COEXIST "MCP health or production bot invariant failed during coexistence"
      fi
    else
      fail MCP_COEXIST "$startup_line"
      RSS_BEFORE=0
    fi
  else
    fail MCP_COEXIST "MCP supervisor did not become ready within ${MCP_STARTUP_TIMEOUT}s"
    RSS_BEFORE=0
  fi

  if [[ "${RESULTS[MCP_COEXIST]:-FAIL}" == PASS ]]; then
    RSS_AFTER="$(sum_tree_rss_kb "$MCP_PID")"
    RSS_DELTA=$((RSS_AFTER - RSS_BEFORE))
    if ((RSS_AFTER <= 1048576 && RSS_DELTA <= 262144)); then
      pass MEMORY "mcp=$((RSS_AFTER / 1024))MiB delta=$((RSS_DELTA / 1024))MiB"
    else
      fail MEMORY "mcp=$((RSS_AFTER / 1024))MiB delta=$((RSS_DELTA / 1024))MiB"
    fi
  fi
  stop_mcp
fi

if [[ "${RESULTS[PROD_HEALTH]:-FAIL}" == PASS ]]; then
  SOURCE_AFTER="$(source_fingerprint || true)"
  if [[ "$SOURCE_AFTER" == "$SOURCE_BEFORE" ]]; then
    pass SOURCE_UNCHANGED "production branch/head/core source hashes unchanged"
  else
    fail SOURCE_UNCHANGED "production core fingerprint changed; handoff blocked"
  fi
fi

if [[ "${RESULTS[MCP_COEXIST]:-FAIL}" == PASS && "${RESULTS[MEMORY]:-FAIL}" == PASS && "${RESULTS[SOURCE_UNCHANGED]:-FAIL}" == PASS ]]; then
  section "CONTROLLED WATCHDOG HANDOFF"
  mapfile -t legacy_now < <(legacy_watchdog_pids)
  mapfile -t v150_now < <(v150_watchdog_pids)
  if ((${#v150_now[@]} > 0)); then
    fail WATCHDOG_HANDOFF "V150 watchdog already running unexpectedly pids=${v150_now[*]}"
  elif ((${#legacy_now[@]} > 1)); then
    fail WATCHDOG_HANDOFF "multiple legacy watchdogs pids=${legacy_now[*]}"
  else
    HANDOFF_IN_PROGRESS=1
    if ((${#legacy_now[@]} == 1)); then
      legacy_pid="${legacy_now[0]}"
      info "stopping legacy watchdog pid=$legacy_pid for handoff"
      if stop_pid_gracefully "$legacy_pid" 15; then
        LEGACY_STOPPED=1
      else
        fail WATCHDOG_HANDOFF "legacy watchdog pid=$legacy_pid did not stop; V150 not started"
        ROLLBACK_NEEDED=1
      fi
    fi

    if [[ "${RESULTS[WATCHDOG_HANDOFF]:-}" != FAIL ]]; then
      if start_v150_watchdog; then
        sleep "$HANDOFF_VERIFY_SECONDS"
        mapfile -t legacy_verify < <(legacy_watchdog_pids)
        mapfile -t v150_verify < <(v150_watchdog_pids)
        pane_verify="$(bot_pane_pid || true)"
        lock_verify="$(bot_lock_state || echo UNKNOWN)"
        health_verify="$(local_health_state)"
        if ((${#legacy_verify[@]} == 0 && ${#v150_verify[@]} == 1)) && \
           [[ "$pane_verify" == "$PANE_AFTER_RECOVERY" ]] && \
           [[ "$lock_verify" == HELD ]] && \
           [[ "$health_verify" == HEALTHY ]]; then
          pass WATCHDOG_HANDOFF "V150 owns watchdog pid=${v150_verify[0]}; legacy=0"
          V150_PID="${v150_verify[0]}"
        else
          fail WATCHDOG_HANDOFF "verify legacy=${legacy_verify[*]:-none} v150=${v150_verify[*]:-none} pane=$pane_verify lock=$lock_verify health=$health_verify"
          ROLLBACK_NEEDED=1
        fi
      else
        fail WATCHDOG_HANDOFF "failed to start V150 watchdog launcher"
        ROLLBACK_NEEDED=1
      fi
    fi
  fi
fi

if [[ "${RESULTS[WATCHDOG_HANDOFF]:-FAIL}" == PASS ]]; then
  sleep 5
  if [[ "$(bot_session_state)" == PRESENT && "$(bot_pane_pid || true)" == "$PANE_AFTER_RECOVERY" && \
        "$(bot_lock_state || echo UNKNOWN)" == HELD && "$(local_health_state)" == HEALTHY ]]; then
    pass BOT_STABILITY "tmux pane, singleton lock and health stable after watchdog handoff"
    HANDOFF_COMMITTED=1
  else
    fail BOT_STABILITY "production invariant changed after handoff"
    ROLLBACK_NEEDED=1
  fi
else
  fail BOT_STABILITY "handoff not completed"
fi

if ((ROLLBACK_NEEDED == 1)); then
  section "WATCHDOG ROLLBACK"
  if rollback_watchdog_owner; then
    HANDOFF_COMMITTED=1
    pass ROLLBACK "V150 stopped and legacy watchdog restored as fallback; recovered bot left running"
  else
    fail ROLLBACK "automatic watchdog rollback incomplete; inspect report immediately"
  fi
else
  pass ROLLBACK "not required"
fi

section "FINAL CLEANUP / INVARIANTS"
stop_mcp
cleanup_ok=1
if [[ "$(bot_session_state)" != PRESENT ]]; then cleanup_ok=0; fi
if [[ "$(bot_lock_state || echo UNKNOWN)" != HELD ]]; then cleanup_ok=0; fi
if [[ "$(local_health_state)" != HEALTHY ]]; then cleanup_ok=0; fi
if ((ROLLBACK_NEEDED == 0)); then
  mapfile -t final_legacy < <(legacy_watchdog_pids)
  mapfile -t final_v150 < <(v150_watchdog_pids)
  if ((${#final_legacy[@]} != 0 || ${#final_v150[@]} != 1)); then cleanup_ok=0; fi
fi
SOURCE_FINAL="$(source_fingerprint || true)"
if [[ "$SOURCE_FINAL" != "$SOURCE_BEFORE" ]]; then cleanup_ok=0; fi

if ((cleanup_ok == 1)); then
  pass CLEANUP "temporary MCP stopped; production bot healthy; source fingerprint preserved"
else
  fail CLEANUP "final invariant failure"
fi

section "FINAL SUMMARY"
for key in "${RESULT_ORDER[@]}"; do
  printf '%-22s %-5s %s\n' "$key" "${RESULTS[$key]:-SKIP}" "${DETAILS[$key]:-not executed}"
done
if ((OVERALL_FAIL == 0)); then
  echo "OVERALL                PASS"
else
  echo "OVERALL                FAIL"
fi

echo "END: $(date)"
echo "REPORT: $REPORT"
print_debug_tail

if ((OVERALL_FAIL == 0)); then
  exit 0
fi
exit 1
