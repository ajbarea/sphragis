# TIGRIS home, before the results layout (2026-09-14 to 2026-09-19)

Diagnostic and pilot scripts that ran from the cluster home directory and were never
committed, kept beside their logs in `datasets/logs/` (jobs 142177 to 143496). `attic/`
held the throughput benchmark behind the research log's 7B throughput entry (job 143201)
and the cursor-160 NaN diagnostics (`diag*.py`); `home/` held the first pilot job and a
Ray thread-count probe. They ran under the `rc-onboard` account, which RC later restricted
to training. The pilot's example file (old salt) is not kept: it is corpus text, superseded
and regenerable.
