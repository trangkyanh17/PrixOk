---
name: api-troubleshooter
description: "Troubleshoot HTTP/API failures including authentication, authorization, rate limits, schema errors, timeouts, provider outages, and bad responses. Use when an API request fails or returns 4xx/5xx or malformed output."
metadata:
  atri-privacy: "auto"
  atri-worker-eligible: "true"
  atri-risk: "medium"
  atri-model-hint: "coding"
  atri-triggers: "api error; lỗi api; loi api; 401 api; 403 api; 429 api; 500 api; 502 api; 503 api; rate limit api; api timeout; api auth; invalid api response"
---

# API Troubleshooter

Classify the failure before changing retry or fallback behavior.

## Workflow

1. Identify endpoint/provider/model, request phase, status code, and whether the failure is deterministic.
2. Classify:
   - DNS/network/TLS,
   - authentication (401),
   - authorization/policy (403),
   - not found/model ID (404/410),
   - validation/schema (400/422),
   - rate limit/quota (429),
   - upstream/provider failure (5xx),
   - timeout/empty/malformed response.
3. Check official provider documentation for current error semantics when the behavior is time-sensitive.
4. Keep model-specific failures local unless evidence shows an account/provider-wide condition.
5. Honor Retry-After or provider reset metadata when present.
6. Validate the response body before treating HTTP 200 as success.
7. Do not expose keys, bearer tokens, cookies, signed URLs, or service-account contents.

## Output

Give classification, evidence, safe retry/fallback policy, and a focused test.
