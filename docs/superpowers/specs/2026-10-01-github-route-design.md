# A GitHub collection route

Status: design, 2026-10-01; decided 2026-10-04. Follows the confirmation sizing (research log, "The
confirmation pass"): Apache and LLVM are of H1-cell size, .NET and Grafana of RQ2 size. AJ asked for at least six organizations so
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
| patch set k | the k-th commit ever on the PR in commit-time order: its timeline commits, every force push's before and after commit, and every commit a review comment names | the final commit list alone loses commits amended or rebased away |
| inline comment on patch set k, line n | review comment whose `original_commit_id` is commit k, `original_line` n on the `RIGHT` side, `path` | every comment, replies included, as Gerrit lists them; the build groups by hunk |
| successor patch set k+1 | the first commit made after the earliest comment on (k, file) whose version of the file differs from k's | GitHub commits are increments; a file untouched later is an empty diff (`no_anchored_hunk`), as on Gerrit |
| Gerrit diff of a file, k to k+1 | git's diff of the two file versions, fetched by commit and path at collection and carried in the row | see "Diff conversion"; the build runs offline, as on the NoteDb route |
| owner, comment author | PR author, review comment author, as `{_account_id: <numeric id>}`, pseudonymised at ingestion with the corpus salt | same `scrub`; `@login` and `@org/team` mentions in prose pseudonymised too (`scrub_mentions`), code spans left alone |
| `SERVICE_USER` tag | `type == "Bot"`, or a login on the registered AI-agent and bot list | mapped onto the tag, so `is_service_user` drops it unchanged |

## Decisions

1. **Reuse, not a second pipeline.** A new module `sphragis/corpus/github.py` fetches and shapes;
   `build.py`, `examples.py`, `refine.py` and `dedup.py` are untouched. Editing them would change
   `BUILD_RULES` and `RULES_VERSION` and stale every frozen month.
2. **Its own rules digest.** The route's shaping is rule code too, so `github.py` gets a
   `GITHUB_RULES` digest recorded on every GitHub month and snapshot, the way `FETCH_RULES` is
   recorded on git-route months. A changed adapter then stales only GitHub months.
3. **Diff conversion.** Both file versions are fetched by commit and path and diffed with git
   (`unified_patch`), never through the compare API, which diffs from the merge base and so
   misplaces a force-pushed successor. git's hunks carry three lines of context, the build's
   `CONTEXT_LINES`. Each hunk becomes its real context as an `ab` block either side of its
   `a`/`b` blocks, and the unchanged lines between hunks become an `ab` block of the right length
   for line accounting. `_context` reads only the block next to a change, so that padding is never
   read as text. A binary or truncated file is counted `text_unavailable`; a file absent at the
   successor (a rename or deletion) `file_gone`.
4. **Suggestion blocks** are reviewer-written targets. `refine.reviewer_wrote_target` already
   matches the fence Gerrit and GitHub share, so they are removed there, as `suggested_edit`, with
   a test on a GitHub-shaped comment rather than a second rule.
5. **AI-authored work is out.** PRs opened by a bot or AI agent are dropped at the snapshot and
   counted; AI reviewers map onto `SERVICE_USER`. The list of agent logins is registered and
   versioned with the route, because this population changes month to month and is a large share of some organizations'
   PRs (research log, "The confirmation pass").
6. **Force pushes and upstream edits.** Patch sets include every commit a force push rewrote away
   and every commit a comment names, so a comment is unplaced (`rewritten_history`) only when
   GitHub no longer holds its commit. Fork points are taken against the base branch as it stood
   before the PR merged (the merge commit's first parent). When the base branch's own version of
   the commented file differs between the two commits' fork points, or a merge commit lies
   between them, the successor carries upstream edits as well as the author's, so the comment is
   dropped (`upstream_change`): the rebase guard the Gerrit routes apply through a revision's
   kind. A file changed after the commented commit but before the comment is an outdated view
   (`outdated_view`), since those edits are no response to it.
7. **Withdrawn content.** A PR or comment the API now answers 404 for is not built, and the
   release-time check proposed for Gerrit (ROADMAP Plan A) covers GitHub too.
8. **Politeness.** Authenticated requests only, GraphQL where it saves calls, conditional requests
   with ETags, the secondary-rate-limit `Retry-After` honoured, no parallel clients.
9. **Terms.** GitHub's Acceptable Use Policies allow research use of "public, non-personal
   information" when the publications are open access. Review threads are personal, so section 8
   governs them instead (corrected 2026-10-08, research log): every paper using this data is posted
   to arXiv and takes the publisher's open-access option (EMSE for the registered report's Stage 2).

## Testing

Recorded fixtures of real API responses, replayed offline (the suite refuses sockets). Tests: the
diff conversion against hand-checked hunks; a PR with a force push; a suggestion block reaching
`reviewer_wrote_target`; a bot reviewer reaching `is_service_user`; an AI-authored PR dropped at the
snapshot; a 404 counted; and the identity scrub on every GitHub field that names a person.

## Decided (2026-10-04)

The research log entry of this date gives the reasons and sources.

- **H1 stays intersection-union over the admitted Gerrit organizations.** How many organizations
  show the effect is read beside it by the partial conjunction, and a random-effects summary over
  every organization read (REML, Hartung-Knapp-Sidik-Jonkman interval from three, a prediction
  interval from five, per-platform subgroups descriptive) is reported, neither binding a verdict.
- **The GitHub organizations are a registered replication on a second platform**, not H1
  candidates: GitHub review is often optional by project setting, which would confound a GitHub
  null with platform, and their cells cannot be powered by Stage 1. Members are fixed at Stage 1:
  Apache, LLVM, .NET, Grafana, each only if frozen and split-checked at N by 2026-11-20. Read on H1
  at one-sided 0.0125, K and the bound fixed per cell before its test window is fetched; the
  outcome is the partial conjunction r over the family, binding no verdict.
