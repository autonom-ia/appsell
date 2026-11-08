# GitHub Actions cost containment — July 2026

## Verified baseline

GitHub's official July total for AppSell is 6,430 minutes. Reconstructing every
job with `ceil(job_seconds / 60)` explains 6,312 minutes (98.16%):

| Workflow | Reconstructed minutes |
| --- | ---: |
| App CI | 4,699 |
| Agent Harness Check v2 | 856 |
| EasyPanel native images | 508 |
| VPS preview artifacts | 249 |

The unexplained 118 minutes remain explicit uncertainty. They are not counted
as savings.

App CI spent 4,128 minutes on pull requests and another 571 after pushes to
`main`. Eight Harness microjobs consumed 856 rounded minutes for 83.78 raw
minutes. Fifty-two overlapping App CI runs were observed; the corrected upper
estimate before cancellation latency is 338 billable minutes, not 401.

## Design

`App CI` now exposes exactly one stable job/check, `ci-gate`, on every event.
Routing, selected suites, component image builds and final enforcement are
steps on the same runner. This avoids one-minute rounding for a dispatcher and
another one-minute rounding for an aggregator while retaining fail-closed
behavior.

Events have separate responsibilities:

- `pull_request`: local diff routing plus fast web/API checks;
- `merge_group`: full conditional certification of the integrated SHA,
  including backup/restore and preview consumer contracts when those paths
  change;
- `push main`: conditional certification plus source smoke while merge queue is
  not explicitly active; source smoke only after the guarded transition;
- weekly schedule: capacity calibration only;
- manual dispatch: full certification for incident/release investigation.

The PR web path runs `test:fast`: all unit/component tests in parallel, excluding
the nine integration files that start real Next processes and mutate generated
Next config. A structural test discovers every file that directly starts Next
and requires it to remain on that exclusion list. The complete 408-test suite,
including those nine files serially, remains mandatory on the integrated SHA.

Only pull-request runs use `cancel-in-progress`. Pull requests, merge groups,
pushes and schedules have different concurrency keys and cannot cancel one
another.

The default for `APPSELL_MERGE_QUEUE_ACTIVE` is deliberately fail-safe: absent
or any value other than the exact string `true` keeps conditional certification
on `push main`. The repository variable may be set to `true` only after the
workflow is on `main`, the approved ruleset/merge queue is active, and a real
merge-group canary has passed. That removes duplicate post-merge certification
without opening a transition window where a normal merge receives only smoke.

The required workflow deliberately has no top-level `paths` filter. A docs-only
change still emits `ci-gate=success`. Unknown files, routing failures,
`compose.yaml`, the CI router and the required workflow fall back to all scopes.

The migration chain shape (`migration_shape`, #551) is expected on every event,
without a route condition: one Alembic head, no repeated migration number, no
revision longer than the 32 characters of `alembic_version`. It reads the files
with `ast` and treats anything it cannot read as a failure. On `pull_request` it
runs on `refs/pull/N/merge`, the PR merged with `main` at the time of the run.
That result goes stale when `main` moves; see "Merge queue is not available"
below for what closes that gap.

## Router contract

The router uses local Git history, never the pull-request files API:

- PR: `base...head`;
- merge group: `base_sha..head_sha`;
- push: `before..sha`;
- `git diff --name-status -z --find-renames` preserves spaces, deletions and
  both sides of renames;
- missing revisions or parse failures choose full certification.

Storage uses an explicit module/test/script list rather than filename keywords.
Capacity may satisfy the dedicated storage vertical only when capacity is
required on the same SHA. The gate treats any expected `skipped`, `cancelled`,
`failure`, missing or unknown outcome as failure.

Any changed workflow runs checksum-pinned Actionlint. The installed Harness
2.1.2 workflow receives YAML/expression/action validation with ShellCheck
disabled only for that one legacy file because it has two pre-existing style
notices; reviewed upstream PR #16 makes the consolidated 2.1.3 workflow fully
clean. API source changes also select the bootstrap image because that
Dockerfile copies the complete `apps/api/src` tree. PostgreSQL evidence is
uploaded only after its redaction scanner succeeds; a failed scan cannot
publish the rejected file.

The real multiprocess BFF test retains both independent Next processes. Local
measurement showed cold startup could exceed its former 15-second readiness
deadline and that an unbounded local `fetch` could ignore that deadline. Each
poll/request now has an abort signal, startup gets 30 seconds per process, and
the outer 90-second guard is only a last resort. Successful clean executions
measured 55–61 seconds under current local load; the change prevents false failures and expensive
reruns rather than adding work.

The classifier was replayed against the file manifests of 64 July pull
requests: all 64 received a deterministic route, zero fell into the unknown
fallback, and eight were identified as documentation/Harness-only. Unknown and
future paths remain covered by dedicated fail-safe fixtures.

## Expensive workflows

Component images moved into the integrated-SHA certification and are selected
independently. The old EasyPanel workflow is manual for image/bootstrap
rehearsals and weekly for backup/restore. The legacy preview artifact workflow
is manual-only; no runbook or artifact is deleted by this change. Manual
preview artifacts keep strict branch/SHA/event provenance: the issue input must
match an allowlisted Issue 66 or 77 branch, and the local consumer explicitly
accepts only `push` or `workflow_dispatch`. Both contracts are exercised before
merge. Scheduled backup runs and manual rehearsals use separate concurrency
groups and never cancel one another.

Artifacts containing already-redacted capacity/PostgreSQL evidence are retained
for seven days instead of thirty. Docker cache export uses `mode=min`.

## Forecast

The initial planning range is 1,200–1,600 minutes/month:

```text
225 PR updates x approximately 1–2 minutes
+ about 30 integrated certifications x approximately 15–20 minutes
+ 107–120 minutes of consolidated Harness checks
+ 31 main smokes x approximately 1–2 minutes
+ 150–250 minutes of conditional/manual image work
+ about 28 minutes of weekly capacity calibration
+ 118 minutes of unexplained residual
```

This is an observability target, not a functional acceptance criterion. The
950–1,200 range is only eligible to become a forecast after two weeks measuring
real job duration, route selection, cancelled runs, merge-group rebuilds and
component image cache behavior.

Before merge queue activation, post-merge conditional certification takes the
place of merge-group certification in this estimate. During the canary
transition, one change may run both; the repository variable remains false
until that canary succeeds.

## Settings gate

This code change does not protect `main` or activate merge queue. After the
workflow is merged and PR canaries prove the real `ci-gate` check name, a new
explicit approval is required before repository settings change. The future
ruleset must require independent review, stale-review dismissal, approval of
the latest push, `ci-gate`, merge queue, blocked force-push/deletion and minimal
bypass. A real merge-group canary must pass before normal queued merges.
Only then may the separately approved repository variable
`APPSELL_MERGE_QUEUE_ACTIVE` become `true`.

## Merge queue is not available (05/11/2026)

Adding the `merge_queue` rule to the `main` ruleset returned `422 Invalid rule
'merge_queue'`. The repository belongs to a personal account (`owner.type=User`),
and GitHub offers merge queue only to repositories owned by an organization.
This is a platform fact, not a pending decision. As a result:

- the ruleset kept `required_status_checks` (`ci-gate`), `non_fast_forward` and
  `deletion`, unchanged;
- `APPSELL_MERGE_QUEUE_ACTIVE` was not created, so `push main` keeps the full
  conditional certification (the fail-safe mode described above). Do not
  create it while there is no queue: it would reduce `push main` to smoke with
  nothing certifying the integrated SHA before it;
- the `merge_group` paths in `app-ci.yml` stay in place, harmless and unused,
  for a future move to an organization.

**Replacement, active since 05/11/2026 (Rodrigo's OK):**
`strict_required_status_checks_policy=true` in the same ruleset; the previous
ruleset was backed up before the change. A PR has to be up to date with `main`
before it can merge, and the Sell orchestration runs the update-branch at merge
time. Review verdicts apply to the head after that update; an update that
brings a new migration or web change gets its CI read again. `ci-gate` (including
`migration_shape`) runs on the integrated result. Example: if one PR adds `0066`
on top of `0065` and merges, a second PR adding `0067` on top of `0065` is forced
to update; its new run sees two heads and fails until it points to `0066`. The
cost is one update-branch, and one PR CI run, per merge.
