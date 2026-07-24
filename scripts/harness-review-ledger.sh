#!/usr/bin/env bash
set -euo pipefail

command_name="${1:-}"
ledger="${2:-}"
manifest="${3:-platform-manifest.json}"

usage() {
  cat <<'EOF'
Uso:
  harness-review-ledger.sh validate <ledger.json> [platform-manifest.json]
  harness-review-ledger.sh summary <ledger.json> [platform-manifest.json]

Valida e resume somente metadados sanitizados de convergência de review.
EOF
}

if [[ "$command_name" != "validate" && "$command_name" != "summary" ]]; then
  usage >&2
  exit 2
fi
if [[ -z "$ledger" || ! -f "$ledger" ]]; then
  echo "Ledger JSON não encontrado." >&2
  exit 2
fi
if [[ ! -f "$manifest" ]]; then
  echo "Manifesto não encontrado." >&2
  exit 2
fi
if ! jq empty "$ledger" >/dev/null 2>&1 || ! jq empty "$manifest" >/dev/null 2>&1; then
  echo "Ledger ou manifesto contém JSON inválido." >&2
  exit 1
fi

max_waves="$(jq -r '.orchestration.review_convergence.max_repair_waves // 2' "$manifest")"
max_final_reviews="$(jq -r '.orchestration.review_convergence.final_broad_reviews // 1' "$manifest")"
if ! [[ "$max_waves" =~ ^[0-9]+$ && "$max_final_reviews" =~ ^[0-9]+$ ]]; then
  echo "Limites de convergência inválidos no manifesto." >&2
  exit 1
fi

validate_ledger() {
  jq -e \
    --argjson max_waves "$max_waves" \
    --argjson max_final_reviews "$max_final_reviews" '
      def sha:
        type == "string" and test("^[0-9a-f]{40}([0-9a-f]{24})?$");
      def digest:
        type == "string" and test("^[0-9a-f]{64}$");
      def nonnegative:
        (type == "number") and . >= 0;
      def forbidden_key:
        test("(^|_)(prompt|response|secret|cookie|credential|authorization|token_value)(_|$)"; "i");
      def valid_usage:
        type == "object"
        and all(
          .input_tokens,
          .output_tokens,
          .cache_read_tokens,
          .cache_write_tokens;
          . == null or nonnegative
        );
      def valid_reviewer:
        type == "object"
        and (.role | type == "string" and length > 0)
        and (.status | IN("completed", "skipped"))
        and (.model_class | IN("fast", "medium", "strong", "human"))
        and (.turns | nonnegative)
        and (.duration_seconds | nonnegative)
        and (.usage | valid_usage);
      def valid_finding:
        type == "object"
        and (.id | type == "string" and test("^F-[0-9]{3,}$"))
        and (.fingerprint | digest)
        and (.severity | IN("P0", "P1", "P2", "P3", "P4"))
        and (.category | type == "string" and length > 0)
        and (.status | IN("new", "open", "carried", "resolved", "reopened", "accepted"))
        and (.first_seen_round | type == "number" and floor == . and . >= 1)
        and (.last_seen_round | type == "number" and floor == . and . >= .first_seen_round);
      def valid_wave:
        type == "object"
        and (.round | type == "number" and floor == . and . >= 1)
        and (.status | IN("planned", "in_progress", "completed"));

      type == "object"
      and ([.. | objects | keys[] | select(forbidden_key)] | length == 0)
      and (.schema_version == "1.0")
      and (.review_id | type == "string" and length > 0)
      and (.orchestration_owner | IN("harness", "superpowers"))
      and (.risk_class | IN("lean", "standard", "critical", "adversarial"))
      and (.round | type == "number" and floor == . and . >= 1)
      and (.base_sha | sha)
      and (.head_sha | sha)
      and (.diff_hash | digest)
      and (.reviewers | type == "array" and length > 0 and all(.[]; valid_reviewer))
      and (
        if (.risk_class == "critical" or .risk_class == "adversarial")
        then all(.reviewers[]; .model_class == "strong" or .model_class == "human")
        else true
        end
      )
      and (.join_status | IN("pending", "complete"))
      and (
        if .join_status == "complete"
        then all(.reviewers[]; .status == "completed" or .status == "skipped")
        else true
        end
      )
      and (.repair_waves | type == "array" and length <= $max_waves and all(.[]; valid_wave))
      and ([.repair_waves[].round] | length == (unique | length))
      and (if (.repair_waves | length) > 0 then .join_status == "complete" else true end)
      and (.findings | type == "array" and all(.[]; valid_finding))
      and ([.findings[].fingerprint] | length == (unique | length))
      and (.final_broad_reviews | type == "number" and floor == . and . >= 0 and . <= $max_final_reviews)
      and (.decision | IN(
        "awaiting_join",
        "repair",
        "delta_review",
        "final_review",
        "ready_for_human",
        "stop_replan",
        "blocked"
      ))
      and (.telemetry | valid_usage)
      and (.telemetry.turns | nonnegative)
      and (.telemetry.duration_seconds | nonnegative)
    ' "$ledger" >/dev/null
}

if ! validate_ledger; then
  echo "Ledger de review inválido ou fora dos limites de convergência." >&2
  exit 1
fi

if [[ "$command_name" == "summary" ]]; then
  jq -r \
    --argjson max_waves "$max_waves" \
    --argjson max_final_reviews "$max_final_reviews" '
      [
        "review_round=\(.round)",
        "join_status=\(.join_status)",
        "repair_waves=\(.repair_waves | length)/\($max_waves)",
        "findings_open=\([.findings[] | select(.status != "resolved" and .status != "accepted")] | length)",
        "findings_resolved=\([.findings[] | select(.status == "resolved")] | length)",
        "final_broad_reviews=\(.final_broad_reviews)/\($max_final_reviews)",
        "decision=\(.decision)",
        "turns=\(.telemetry.turns)",
        "duration_seconds=\(.telemetry.duration_seconds)"
      ] | .[]
    ' "$ledger"
fi
