# A rules-file comparator

Status: design, 2026-10-05; implemented 2026-10-06 (plan of record: research log, 2026-10-06). Registered as an exploratory comparator on 2026-10-04 (registered
decisions, "Comparators and audits beside the confirmatory test"). Built on the retrieval
comparator's runner (#77), with each pool replaced by a rules file.

## Goal

Ask whether a declared rules file recovers what the adapters learn. A rules file is the form in
which coding agents take conventions today (AGENTS.md and its kin). It is measured on the same
examples, with the same exact match and the same contrasts as H1, and binds no verdict. Context
files are followed well as instructions, while repository overviews do not help task success
(Gloaguen et al., arXiv:2602.11988v3). So a file here holds rules only.

## Arms

On each development-window example of an evaluated half, over the first ten admissible
partitions (`retrieval.PARTITIONS`):

| arm | rules file | contrast it enters |
|---|---|---|
| distilled own | distilled from the half's own pool | own minus sibling (H1's counterpart) |
| distilled sibling | distilled from the sibling half's pool | own minus sibling; sibling minus foreign |
| distilled foreign | from each half of the other organization's first admissible partition, averaged per example | sibling minus foreign |
| written own | the organization's own written conventions | written own minus written foreign; minus none |
| written foreign | the other organization's written conventions | written own minus written foreign |
| none | no file: the base arm | reused from the retrieval comparator's foreign job |

A pool is the retrieval comparator's: the rows the half's adapter trains on (`retrieval.pools`).

## Decisions

1. **The distiller is Qwen3.6-27B** (pinned, bf16, greedy, thinking off), the newest dense model
   of the evaluated model's family that fits one GH200. The base model was the first choice, to
   keep a second model's prior out of the file, and four smoke jobs showed it cannot distil
   grounded rules from reviews: it ignored the per-chunk limit, cited changes that did not show
   the rule, and its merge added tools no list named. A file it wrote would be a strawman, so a
   stronger distiller writes the files; every arm is still generated and scored by the evaluated
   model. The prior a larger model brings is the price of a file that says what the reviews say.
2. **One pipeline for both sources.** The written conventions and the review comments go through
   one map and reduce pipeline, with prompts that differ only where the sources do: a review
   merge ranks rules by how many lists they recur in (industrial rule mining's promotion rule,
   Qodo Rule Miner, 2026-07), a guide merge keeps every rule, since a guide states each once.
   Amended 2026-10-06, before any arm was scored: a mined rule's evidence is its chunk's changes
   (two or more), and recurrence across lists ranks rules rather than filtering them, since
   requiring two lists left one to five rules a half (research log, 2026-10-06). The written guides are OpenStack's `hacking` guidelines
   and MediaWiki's coding conventions, snapshotted once with their URL, date and sha256 under
   `datasets/rules/`. Written own minus distilled own then compares sources, not formatting.
3. **Map, then reduce.** Map: pack a pool's rows (each row's review comments with its hunk before
   and after) into chunks under a fixed token budget, and ask for the conventions the reviewers
   enforce, one imperative rule a line. Reduce: merge the chunk lists into one file, duplicates
   removed, under the file budget. Both prompts are committed before any run.
4. **File budget: 2,048 tokens**, one training example's length (`model.TRAINING`
   `max_seq_length`). A rules file then takes the context one retrieved shot takes at k = 1.
   The comparison sits at the retrieval comparator's smallest registered context, not at a
   length chosen after seeing files.
5. **The file goes in the system turn**, after the model's default system prompt, where coding
   agents load a rules file; the user turn is the base arm's prompt unchanged.
6. **One runner and one reader.** `retrieval_comparator.py --arms rules` and
   `retrieval_read.py --arms rules` read the arm families (`retrieval-k1` and `retrieval-k3`,
   or `rules-distilled`) with the same estimator, reading rule and SESOI; the rules reading takes
   its base arm from the retrieval foreign job. The written arms are organization-level, so they
   enter paired contrasts per half (`paired_clusters`), not the half split.

## Cost, before it is spent

Evaluation runs 2 generations an example a partition and 4 an example once (distilled foreign at
two halves, written own, written foreign). On the pilots' 501 and 711 examples that is 12,024 and
17,064 generations. Distillation is about a thousand long generations over the 22 pools and 2
guides. The first job measures the actual rate.

## The written guides, measured

Fetched 2026-10-05: OpenStack's `HACKING.rst` is 1,795 words. MediaWiki's general conventions with
its PHP, JavaScript and CSS pages are 16,876 words (`wc -w`). Their token counts are measured with
the model's tokenizer before the prompts are fixed; MediaWiki's cannot fit the 2,048-token budget
at any plausible ratio, and both go through the reduce either way (decision 2). Which MediaWiki language pages enter is
read from the languages of Wikimedia's training window before any distillation.

## Open before implementation

- The map chunk budget: the largest that keeps a map prompt and its answer inside the model's
  32,768-token context (its config's `max_position_embeddings`), with room left. Measured
  2026-10-06 on each organization's first partition, equalized to N before the budget filter,
  with the model's tokenizer: a half's rows (prompt and revised code) total 228,632 to 241,487
  tokens for OpenStack and 309,974 to 337,301 for Wikimedia, median 89 to 105 a row; their review
  comments alone total 73,021 to 97,467. A pool is therefore some ten to fourteen map chunks of
  rows, or four of comments alone. The budget filter (2,048 tokens a row) lowers both.
- Whether a pool of 1,850 rows is distilled from every row or from a fixed seeded subsample, if
  the map pass is too long for one job's wall clock.
