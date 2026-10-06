# A rules-file comparator

Status: design, 2026-10-05. Registered as an exploratory comparator on 2026-10-04 (registered
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

1. **The distiller is the base model** (Qwen2.5-Coder-7B-Instruct, pinned, greedy, fp32). A
   frontier distiller would bring in what it already knows about OpenStack and MediaWiki, and the
   comparison would then credit the rules file with a second model's prior. One model also keeps
   every arm offline on the cluster.
2. **One pipeline for both sources.** The written conventions and the review comments go through
   the same fixed map and reduce prompts. The written guides are OpenStack's `hacking` guidelines
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
5. **One reader.** `retrieval_read.py` takes the conditions it reads (`retrieval-k1`,
   `retrieval-k3`, `rules-distilled`), with the same estimator, reading rule and SESOI. The
   written arms are organization-level, so they enter a per-example paired contrast, not the
   half-split.

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

- The map chunk budget, measured on the pools: the largest that keeps a map prompt and its answer
  inside the model's context, with room left.
- Whether a pool of 1,850 rows is distilled from every row or from a fixed seeded subsample, if
  the map pass is too long for one job's wall clock.
