# A GitHub collection route

Status: design, 2026-10-01. Follows the confirmation sizing (research log, "The confirmation pass"):
Apache and LLVM reach an H1 cell, .NET and Grafana RQ2. AJ asked for at least six organizations so
that RQ2 is a real federation.

## Goal

Collect pull-request review from GitHub organizations into the same built, refined and frozen
corpus the Gerrit hosts produce, so every downstream stage (refine rules, dedup, split criteria,
partition runs, the outcome-neutral checks) runs unchanged. The route is an adapter: it presents a
pull request as a Gerrit-shaped change and answers `build_from_change`'s two fetchers, as the
NoteDb route's `embedded_fetchers` already does.

## Mapping

| Gerrit | GitHub | Notes |
|---|---|---|
| change | merged pull request (OpenJDK: integrated, closed by its bot) | window month by PR creation, as Gerrit's by change creation |
| project | repository; for LLVM, the top-level directory of `llvm/llvm-project` | LLVM's definition registered before its split is read |
| patch set k | the PR head after its k-th commit, in the PR's final commit list | |
| inline comment on patch set k, line n | review comment whose `original_commit_id` is commit k, `original_line` n, `path` | thread starts only; replies are the conversation, not new anchors |
| successor patch set k+1 | commit k+1 | a comment on the last commit has none, dropped `no_successor` |
| Gerrit diff of a file, k to k+1 | compare API patch of that file between commits k and k+1, converted to `content` blocks | see "Diff conversion" |
| owner, comment author | PR author, review comment author, pseudonymised at ingestion with the corpus salt | same `scrub` |
| `SERVICE_USER` tag | `type == "Bot"`, or a login on the registered AI-agent and bot list | mapped onto the tag, so `is_service_user` drops it unchanged |

## Decisions

1. **Reuse, not a second pipeline.** A new module `sphragis/corpus/github.py` fetches and shapes;
   `build.py`, `examples.py`, `refine.py` and `dedup.py` are untouched. Editing them would change
   `BUILD_RULES` and `RULES_VERSION` and stale every frozen month.
2. **Its own rules digest.** The route's shaping is rule code too, so `github.py` gets a
   `GITHUB_RULES` digest recorded on every GitHub month and snapshot, the way `FETCH_RULES` is
   recorded on git-route months. A changed adapter then stales only GitHub months.
3. **Diff conversion.** The compare API gives unified hunks with three lines of context, the
   build's `CONTEXT_LINES`. Each hunk becomes its real context as an `ab` block either side of its
   `a`/`b` blocks, and the unchanged lines between hunks become an `ab` block of the right length
   for line accounting. `_context` reads only the block next to a change, so that padding is never
   read as text (test: a converted diff yields the same hunks and context as a Gerrit diff of the
   same files). A file whose patch GitHub omits (too large) raises, counted `diff_error`.
4. **Suggestion blocks** are reviewer-written targets. `refine.reviewer_wrote_target` already
   matches the fence Gerrit and GitHub share, so they are removed there, as `suggested_edit`, with
   a test on a GitHub-shaped comment rather than a second rule.
5. **AI-authored work is out.** PRs opened by a bot or AI agent are dropped at the snapshot and
   counted; AI reviewers map onto `SERVICE_USER`. The list of agent logins is registered and
   versioned with the route, because this population changes month to month and is a large share of some organizations'
   PRs (research log, "The confirmation pass").
6. **Force pushes.** A comment whose `original_commit_id` is not in the final commit list has no
   recoverable successor and is dropped, counted apart (`rewritten_history`) so the loss is visible.
7. **Withdrawn content.** A PR or comment the API now answers 404 for is not built, and the
   release-time check proposed for Gerrit (ROADMAP Plan A) covers GitHub too.
8. **Politeness.** Authenticated requests only, GraphQL where it saves calls, conditional requests
   with ETags, the secondary-rate-limit `Retry-After` honoured, no parallel clients.
9. **Terms.** GitHub's Acceptable Use Policies allow research use of public information when the
   publications are open access: every paper using this data is posted to arXiv and takes the
   publisher's open-access option (EMSE for the registered report's Stage 2).

## Testing

Recorded fixtures of real API responses, replayed offline (the suite refuses sockets). Tests: the
diff conversion against hand-checked hunks; a PR with a force push; a suggestion block reaching
`reviewer_wrote_target`; a bot reviewer reaching `is_service_user`; an AI-authored PR dropped at the
snapshot; a 404 counted; and the identity scrub on every GitHub field that names a person.

## Open, for AJ

- H1 is intersection-union over admitted organizations, so each added cell raises every cell's
  power target (0.95^(1/k)) and runs. With four to six cells, keep that rule or register a reading
  per organization with a heterogeneity estimate. Decided before Stage 1 is submitted.
- Whether GitHub organizations enter Stage 1 as candidates behind Qt and Chromium, or as admitted
  organizations, depends on whether their corpora are built, split-checked and piloted by
  2026-11-20.
