# NPLB campaign

Tools to measure how a fabric's per-packet load balancing (NPLB) compares with ECMP, with Blink
microbenchmarks run through CRAB. Every number in the results comes from Blink timings; NIC
counters are read only by the [NIC check](#check-that-each-rank-uses-its-own-nic) before the
campaign.

| File | What it does |
|------|--------------|
| `preflight.sh` | Checks over ssh that every node sees the CRAB checkout, the Blink programs and the same MPI, so ranks can start there. |
| `sr_state.sh` | Reads the Selective Repeat setting of every active NIC on every node (read only). |
| `sr_set.sh` | Sets the Selective Repeat setting of every active NIC on the given nodes and reads it back; see [below](#read-the-nic-side-of-the-network-configuration). |
| `ar_set.sh` | Sets the adaptive routing bit (`adaptive_routing_forced_en` of `ROCE_ACCL`) of every active NIC on the given nodes and reads it back, with the same arguments and output as `sr_set.sh` but labelled `AR=`. Not persistent across reboots. It runs `sr_set.sh` with `ACCL_BIT=ar`, the variable `sr_set.sh` uses to select the bit (`sr`, the default, or `ar`). |
| `probe.py` | Reads each node's RDMA NICs (ports, PCI address, speed, NUMA node, netdev, memory, GPU layout) and writes a cluster profile with the `nics` and `cpus` lists to use. |
| `nic_check.sh` | Runs Blink in several placements on 2 nodes and shows per-rank bandwidth and the bytes each NIC sent, to confirm every rank uses its own NIC. |
| `make_configs.py` | Writes the CRAB configs of both test sets from the profile (`md_set.py`, `colleague_set.py`, `naming.py`). |
| `gpu_set.py` | Writes the GPU comm-only suites (all-to-all and all-gather victims with the buffers in GPU memory, against a steady all-to-all aggressor); see [below](#gpu-comm-only-suites). |
| `campaign.py` | Writes the blocks of the contention campaign on the 4 H100 nodes: `<layout>_<victim>.json` for 3 layouts x 3 victims (9 files), each one Slurm job with one experiment per condition. **Victims** (buffers in GPU memory, `-gpu`): `a2a` = `alltoall_comm_only`, `agtr` = `allgather_comm_only`, `ardc` = `allreduce_b`; the 8 message sizes (16 KiB to 256 MiB, x4) are chained inside each experiment (each app starts when the previous one ends), so the aggressor starts once per experiment, 3 s before the chain, and stops (`end: f`) when the chain is done. **Layouts**: `split-i` is allocation `interleaved` with partitions `victim` and `aggressor` of share 50 (victim on Slurm nodes 0 and 2, aggressor on 1 and 3, 8 ranks per node each); `split-l` is the same partitions with allocation `linear` (victim on 0 and 1, aggressor on 2 and 3); `shared` is allocation `shared` with no partitions (CRAB refuses `shared` together with them), both apps on all 4 nodes with 4 ranks per node each, pinned apart as in `gpu_set.py`: the victim on GPUs 0-3, ports `NICS[0:4]` and cores 0-3, the aggressor on GPUs 4-7 (`CUDA_VISIBLE_DEVICES=4,5,6,7`), ports `NICS[4:8]` and cores 40-43. **Conditions** (the experiment names): `alone`; `steady_a128k`, `steady_a2m`, `steady_a16m` (a steady all-to-all aggressor of that message size, `-endl -warmup 0`); `burst_l<L>ms_p<P>ms_a<size>` (bursts of `L` ms with pauses of `P` ms, `-blength L ms -bpause P ms`): 1, 10 and 100 ms at 50 % duty and `l10_p30` (25 %) and `l30_p10` (75 %), all at 128 KiB, then `l10_p10` at 2 MiB and at 16 MiB. The aggressor is never collected. `-iter` aims at about 20 s per victim point (see `campaign_cost.py`), `-warmup` is a tenth of it (at least 10) and `-maxsamples` equals `-iter`, because Blink keeps only the last 1000 samples otherwise. Walltime is the estimated run time plus 10 s of start-up per app, x1.5, at least 30 min and refused above 6 h; `timeout` is the largest experiment's estimate x5, at least 600 s. Flags: `--out DIR` (required, must be inside a `local/` directory, which is git-ignored), `--layout` and `--victim` (comma lists, default all), `--sizes` (bytes, `K`/`M` allowed, default the 8), `--conditions` (comma list of the names above, written in campaign order), `--local` (2-node laptop blocks to drive the flow: no `-gpu`, `binary`, `nics`, `cpus` or UCX variables, `-iter 5 -warmup 1`, sizes 1 KiB and 1 MiB, layouts `split-i` and `shared`, conditions `alone`, `steady_a128k` and `burst_l10ms_p10ms_a128k`, files `local_<layout>_<victim>.json`). Exit 2 with one line on stderr for a bad name. |
| `campaign_cost.py` | The cost model behind `campaign.py`: seconds per victim iteration, from which `-iter` is chosen. The table holds the median over samples of the slowest rank's time, 16 ranks on 2 nodes, from the October a2a and all-gather runs (the a2a run failed at 256 MiB, which is extrapolated as 4 x the 64 MiB time). Each value comes with a source tag: `measured`; `extrapolated`; `proxy-ppn8` (the `shared` layout reuses the 16-rank numbers); `model-ardc` (all-reduce = `ALLREDUCE_FACTOR` x the all-to-all time, 2.0); `assumed-contention` (a victim against an aggressor takes `CONTENTION_FACTOR` x its alone time, 2.0). The last two factors are planning assumptions, not measurements. `iterations()` returns `-iter` = 20 s / time clamped to 100..20000, and `-warmup` = a tenth of it, at least 10. |
| `campaign_plan.py` | Plans the slot campaign on top of `campaign.py` (also `nplb campaign`): writes the block files and `schedule.tsv`, and prints the estimate report. **Network states**: NPLB 0/1 x AR 0/1 x SR 0/1 (8 labels such as `nplb1_ar0_sr1`, from `net_state.py`); NPLB and AR are switch settings (the owner pastes them by hand, `--switch-min` minutes each time, 10 by default), SR is a NIC setting (`--sr-min`, 2). **Tiers**, victims `a2a`, `agtr`, `ardc`, `C3` = `alone`, `steady_a128k`, `burst_l10ms_p10ms_a128k`, `KEY` = `nplb0_ar0_sr0` and `nplb1_ar0_sr1`: tier 1 is block `t1` (`split-i`, `C3`, sizes 64 KiB, 1 MiB, 16 MiB, 256 MiB) under all 8 states; tier 2 is `t2a` (`split-i`, `C3`, the other four sizes) and `t2b` (`split-i`, the other 8 conditions, all 8 sizes) under `KEY`; tier 3 is `t3` (`split-l` and `shared`, `C3`, all 8 sizes) under `KEY`; tier 4 is `t2a` and `t2b` under the six states that are not `KEY`. No cell (state, layout, victim, condition, size) is measured twice, and a cut-off keeps whole tiers. Block files are `<block>_<layout>_<victim>.json` (15 files), the job name `nplb_<block>_<layout>_<victim>`. **Order**: `--order tier` (default) sorts by tier, then state, layout, victim and block part; inside a tier the first state is the one closest to where the switches were left and each next one the cheapest step (fewest switch changes, then SR changes), the states with NPLB and AR both on always last in the tier (they are checked at the slot and dropped if the switch refuses them). `--order state` makes one pass over the 8 states with the same rule (4 switch changes at most), so a cut-off keeps whole states. A switch change is two consecutive rows with a different (NPLB, AR); the first row counts as one, and SR changes are counted the same way. **`schedule.tsv`** (tab-separated, one row per run of a block under a state, no timestamps): `n tier state nplb ar sr block layout victim est_min switch_change sr_change cum_h file`; `est_min` is the cost model's run time without the walltime margin, `cum_h` the running total in hours with the switch and SR overheads. **Report** (stdout, paged by the verb): the verdict first, a per-tier table (blocks, runs, apps, hours, switch changes, cumulative hours, share of the victim time that rests on an assumption), the biggest assumptions (`CONTENTION_FACTOR`, `ALLREDUCE_FACTOR`, the `shared` ppn8 proxy), the switch changes of the other order, and the leaf classes of `node_map.json` (written by `nplb topo`) when it exists. Flags: `--out DIR` (default `local/campaign`, must be inside a `local/` directory), `--order tier|state`, `--budget-hours H` (the verdict names the whole tiers, or states, that fit), `--estimate` (write nothing), `--local` (laptop rehearsal: one tier `L`, no states, blocks `l_<layout>_<victim>.json` for `split-i` and `shared`), `--switch-min`, `--sr-min`. Steps narrate on stderr. |
| `smoke.py` | Plans the lab smoke test that runs before the campaign, on top of `campaign.py` (`campaign.build_block(..., iters=(20, 2))` fixes `-iter 20 -warmup 2 -maxsamples 20`). **Features**, one block file `smoke_<feature>.json` each (job name `nplb_smoke_<feature>`), victim sizes 64 KiB and 16 MiB: `plain` (`split-i`, `a2a`, `alone` and `steady_a128k`), `bursty` (`split-i`, `a2a`, `burst_l10ms_p10ms_a128k`: a bursty aggressor with `-gpu`), `ardc` (`split-i`, `allreduce_b`), `split-l` (`split-l`, `a2a`) and `shared` (`shared`, `a2a`). `smoke.py features` prints the table; `smoke.py plan [FEATURE...] [--out DIR] [--estimate]` writes the blocks and a `schedule.tsv` in the format of `campaign_plan.py` (tier `S`, `-` in the four state cells, one row per feature in table order, numbered from 1) and prints the report: the verdict first (about 9 minutes for all five), a table, which part rests on the cost model and which on a guess (60 s of Slurm allocation per job), the folder and `next: nplb smoke`. `--out` defaults to `<WORK>/smoke/campaign` (`$WORK`, else `$NPLB_WORK`, else `local/nplb-work`), a folder of its own so that smoke row 1 is never taken for campaign row 1; an explicit `--out` must be inside a `local/` directory. `--estimate` writes nothing. Exit 0, or 2 on an unknown feature or a bad `--out`. `smoke_run.py` is the driver that runs the planned rows. |
| `smoke_run.py` | Behind `nplb smoke [FEATURE...]`, stdlib only. Plans the smoke blocks with `smoke.py plan` into `<WORK>/smoke/campaign`, then runs `nplb run N` for every row in table order, one at a time, each followed to its end, and prints ONE verdict page (saved as `smoke`): `VERDICT: OK smoke: 5 of 5 features passed (23 min)`, `VERDICT: FAIL smoke: bursty failed (4 of 5 passed)` or `VERDICT: INCOMPLETE smoke: stopped at row N (...), K of 5 done`, a table `feature result time proves` (`OK`, `WARN`, `FAIL`, `running`, `not run`) and the next command: `nplb diag run <data dir name>` for the first failed feature, `nplb topo, then nplb campaign` when all passed. A `WARN` counts as passed and is named in the verdict. The features are independent, so a `FAIL` does not stop the later rows; the driver stops when `nplb run` logged no attempt (a refusal or a failed submit: its page is on stderr) or left it `SUBMITTED` (Ctrl-C or a lost follow: the job keeps running, `nplb smoke follow` or `nplb smoke cancel`), and the rest is `not run`. It never forces a row. The smoke test has a work folder of its own: every child runs with `WORK=<WORK>/smoke`, `CAMPAIGN_DIR=<WORK>/smoke/campaign` and `NPLB_REPORT_DIR=<WORK>/smoke/reports`, so its run log and its `run-1`.. pages never mix with the campaign's, and `nplb report` does not list the smoke runs (state `-`). A new run first moves the old `campaign_runs.tsv` and `campaign/` into `<WORK>/smoke/archive/<YYYYmmdd-HHMMSS>/` and refuses to start while the smoke run log has a `SUBMITTED` attempt. `nplb smoke plan [FEATURE...] [--estimate]` is `smoke.py plan` (paged as `smoke-plan`); `nplb smoke follow` and `nplb smoke cancel` are `nplb follow` and `nplb cancel` under the smoke env. Env: `NPLB_BIN` (the dispatcher, default `nplb` next to the file), `NPLB_NOW`. Exit 0 when every feature is `OK` or `WARN`, 1 for `FAIL`, an incomplete run or a refusal to start, 2 for an unknown feature. |
| `run_log.py` | Behind `nplb next` and `nplb queue`: the run log of the campaign and the two read-only verbs built on it, stdlib only. **Files**: it reads `schedule.tsv` (from `campaign_plan.py`, in `$CAMPAIGN_DIR`, default `local/campaign`) and owns `campaign_runs.tsv` in `$WORK` (default `local/nplb-work`; not `run_log.tsv`, which `bringup.sh` uses with another layout). `campaign_runs.tsv` is tab-separated with a header and one line per attempt, `n state block layout victim job data_dir submitted status elapsed_s note`: `n` is the schedule row, `submitted` an ISO time (`$NPLB_NOW` or the clock), `status` one of `SUBMITTED OK WARN FAIL CANCELLED SKIPPED`, `elapsed_s` an integer, empty while the job runs; tabs and newlines in a field become spaces. A line is appended per submit and updated in place through a temp file and a rename (`append_run`, `update_run`, matched by `job`; an unknown job raises `KeyError`; `set_status_where` changes the attempts of given rows with one status, for the job-less `SKIPPED` ones). **Rules**: a schedule row is DONE when an attempt is `OK` or `WARN`, RUNNING when an attempt is `SUBMITTED`, SKIPPED when an attempt is `SKIPPED` (written by `nplb skip` with job and data dir `-`; `next` never proposes the row and the remaining hours leave it out), FAILED when it has attempts but none of those; `CANCELLED` attempts count as none (`OK`/`WARN` beats `SUBMITTED` beats `SKIPPED` beats `FAIL`). **`next`** (paged, saved as `next`): the first row with no attempt (a failed row is listed under `to retry`, never proposed again); the verdict is `VERDICT: NEXT row 4/84: ...`, `VERDICT: RUNNING row N (job J): follow it with: nplb follow` while an attempt is `SUBMITTED`, `VERDICT: RETRY ...` or `VERDICT: DONE all N rows` (`, K skipped` when rows were skipped). Then the network state the row needs against the declared one in `$WORK/net_state.json` (`MISMATCH` lists `nplb net sheet`, `nplb net declare` and `nplb net set sr=...`; the SR command is listed when nothing ran yet or the last attempt ran a row with another SR), `then: nplb run N`, the progress per tier (rows done, failed, skipped (a column that appears once a row is skipped), hours elapsed from `OK`/`WARN`/`FAIL` attempts, hours left) and the remaining hours: the estimates of the rows without an `OK`/`WARN` attempt times the ratio of measured to estimated time of the finished attempts (1.0 with none). Switch and SR change time is not in that figure (it is in `cum_h`). **`queue`** (paged, saved as `queue`): the verb runs `squeue -h -p $PARTITION -o "%i %j %T %M %l %R"` (`PARTITION` default `h100_4nodes`, not user-specific; `SQUEUE` names another command) and `run_log.py queue --squeue-file F [--squeue-exit N]` prints those lines followed by the last 8 runs, newest first; no `squeue` or a failing one gives a line saying so and exit 0. Exit 0, or 2 on a missing or bad schedule or run log (the message names the file and the first wrong column). |
| `follow.py` | Behind `nplb follow [TARGET]`: follows a submitted CRAB job, says how it ended on a cluster without Slurm accounting (`sacct` answers nothing, `crab status` says UNKNOWN for every finished job, so `crab` is not called), narrates it and summarises it; read only, stdlib only, it submits and cancels nothing and runs only `squeue -h -j JOB -o "%T %M"` and `scontrol show job JOB` (`SQUEUE` and `SCONTROL` name other commands). **Target**: a schedule row (its newest attempt, `follow 14`), a job id (a run-log job first, else the `StdOut=` of `scontrol`), a data dir path or a folder name under `$DATA_ROOT` (default `data/nplb_h100_gpu`); none = the newest `SUBMITTED` attempt of `campaign_runs.tsv`, else the newest attempt, else the newest folder under `$DATA_ROOT`. **Files**: reads `<data_dir>/slurm_output.log`, `slurm_error.log`, `config.json` (else the block file of the schedule row in `$CAMPAIGN_DIR`) for the app counts, `<data_dir>/<exp>/error_app_N.log`, `nplb_state.json`, and `metadata.csv` in the parent of the data dir; the only file it writes is `campaign_runs.tsv` in `$WORK` (status, `elapsed_s`, `note` of the attempts of that job, through `run_log.update_run`), and only when the target is a row of it. **Narration** goes to stderr as `[HH:MM:SS] text`: it says first that Ctrl-C only stops following and the job keeps running; then a line per queue state change (`job queued (PENDING), no log yet`), a line per worker message that matters (`Worker started`, `Allocated N node(s)`, `Starting experiment [k/n]: NAME`, app `Launched`, `FINISHED exit=0` and `FAILED exit=N` with `(apps k/N)`, `Converged`, `Data saved to`, `Engine run finished`, any WARN/ERROR/FATAL line of the engine; the benchmark's own output is never echoed), and a heartbeat `job N RUNNING mm:ss, last log line S s ago` after `$FOLLOW_HEARTBEAT` quiet seconds. A log that already has lines is caught up with its last 40 messages. **How a job ended**, in this order once the job is out of the queue: (1) `scontrol` (it knows a job for about 5 minutes after its end): `FAILED`, `TIMEOUT`, `CANCELLED`, `NODE_FAIL`, `OUT_OF_MEMORY`, `BOOT_FAIL`, `DEADLINE`, `PREEMPTED` and `COMPLETED` with an exit code other than `0:0` are FAIL; `COMPLETED 0:0` lets the log decide; any other state means alive and restarts the silence clocks; (2) the last non-empty line of `slurm_output.log`, colour codes stripped: `Engine run finished` is OK; `Worker fatal error`, `FATAL`, `Worker terminated`, `Worker interrupted` are FAIL (`All experiments finished` is neither: the worker prints it after failed experiments too); (3) silence: the log's size unchanged for `$FOLLOW_STALL` seconds with no final line is FAIL `ended without a final line (stopped early?)` with the last line of `slurm_error.log`, and no log that long after the job left the queue is FAIL `job left the queue without writing a log`. An OK becomes WARN when `metadata.csv` lists a `FAILED` experiment of this run (or the log shows a failed app), and the experiments are named. The rules are those of `job_outcome` in `gpu_lab_run.sh`. **Report** (stdout: `#report NAME`, then the report; the verb pages it and saves it as `run-<n>` for a row, else `run-<job>` or `run-<folder>`): `VERDICT: OK row N job J block layout victim state: A apps ok in MmSSs (est X min)` first, the reason, a table of experiments (apps ok/total, wall time), the failed apps with the first error line of their `error_app_N.log`, the data dir, the network state of `nplb_state.json` and `nplb next`. Env: `FOLLOW_POLL` (10 s between polls), `FOLLOW_STALL` (120 s), `FOLLOW_HEARTBEAT` (15 s), `DATA_ROOT`, `WORK`, `CAMPAIGN_DIR`, `NPLB_NOW`. Exit 0 for OK and WARN, 1 for FAIL, 2 for a target or input file that cannot be used (message on stderr), 130 on Ctrl-C. The tests use excerpts of two real October 2026 runs, described in `tests/fixtures/nplb/README.md`. |
| `run_cmd.py` | Behind `nplb run`, `nplb cancel` and `nplb skip`, stdlib only. **`run N\|next [--force] [--no-follow]`** evaluates seven gates, all of them, and reports every failed one (`VERDICT: REFUSED row N: <first reason>`, then one block per gate with the command to run next; exit 1, nothing submitted, the verb pages it as `run-gate`): (1) no attempt of any row is `SUBMITTED` (`nplb follow` or `nplb cancel`); (2) the row is in `schedule.tsv` and its block file is in `$CAMPAIGN_DIR` (`nplb campaign`); (3) the row is not `OK`/`WARN` yet (rerun: `nplb run N --force`; `run next` never reruns and prints `VERDICT: DONE ...` when nothing is left); (4) the declared switch state in `$WORK/net_state.json` is the row's NPLB and AR (the `nplb net sheet`, `declare`, `set sr=` commands are those of `nplb next`); (5) `net_tools.sh record` into `$WORK/staging/row<N>_<time>` works on the nodes (it refuses without a declaration or when nodes disagree) and its NIC `sr` is the row's (`nplb net set sr=on\|off`); (6) `crab validate -p $PRESET BLOCK` exits 0 (its last 5 lines are shown otherwise); (7) `squeue -h -p $PARTITION -t RUNNING,PENDING -o %i` lists nothing, because nodes are exclusive (`--force` waives it; a missing `squeue` is a note). Rows whose state is `-` (local rehearsal) skip gates 4 and 5. With the gates green it runs `crab run -p $PRESET --json BLOCK` from the checkout, copies the state record to `<data_dir>/nplb_state.json`, writes the schedule row to `<data_dir>/schedule_row.json`, appends a `SUBMITTED` attempt (data dir made absolute) to `campaign_runs.tsv`, and prints `submitted: row N job J -> DIR` with `job J` as its last line; the verb then follows the job (`--no-follow` stops after the submit; Ctrl-C only stops following). A failed submit prints `VERDICT: FAIL submit` with the tail of crab's stderr and logs nothing. **`cancel`** shows the newest `SUBMITTED` attempt, asks `Cancel it? [y/N]` (a terminal, or `NPLB_YES=1`; otherwise `declined: nothing changed`, exit 1), runs `scancel JOB` for that job of the run log only and marks the attempt `CANCELLED`; a failing `scancel` leaves it `SUBMITTED`. It never kills a process. **`skip N... \| --state LABEL \| --tier T [--why TEXT] [--undo]`** (numbers and ranges like `9-12`, selectors combined as a union) appends a `SKIPPED` attempt for each selected row that is not done, running or skipped and prints `skipped N rows: 5, 7-9` and `already handled: 3 (done)`; an unknown row exits 2 and changes nothing; `--undo` turns the `SKIPPED` attempts of the selected rows into `CANCELLED`. Env: `NET_TOOLS` (default `net_tools.sh` next to it), `CRAB` (default `.venv/bin/crab`, else `crab`), `PRESET` (`nplb_h100_gpu`), `SQUEUE`, `SCANCEL`, `PARTITION` (`h100_4nodes`), `NPLB_YES`, `WORK`, `CAMPAIGN_DIR`, `NPLB_NOW`. Narration goes to stderr as `[HH:MM:SS] text`. Exit 0, 1 for a refusal, a failed submit, a declined or failed cancel, 2 for a bad command line or input file. |
| `gpu_preflight.sh` | Checks over ssh that every H100 node can run the GPU suites (state, 16 ports, 8 GPUs, `nvidia_peermem`, CUDA-aware MPI and UCX, Blink programs, NFS, Selective Repeat), and diffs the NIC counters before and after a run; read only, see [below](#check-the-h100-nodes-before-a-gpu-run). |
| `report.py` | Behind `nplb report` and `nplb compare`: the lab analysis of the finished runs, read only (it writes nothing), stdlib only. **`report [N \| --state LABEL \| --victim V \| --layout L \| --all] [--run DIR ...]`** prints slowdown tables: page 1 is a summary with the verdict first, then one table per state, layout and victim (rows = the victim's message sizes, columns = `alone ms` and the conditions, cell = median time divided by the median of the `alone` run of the same state, layout, victim and size). With no argument the scope is the state of the newest finished run of the run log; `N` takes the runs of schedule row N, `--state` one state (`nplb1_ar0_sr1` or `n1a0s1`), `--all` every state; `--victim` and `--layout` alone take every state and narrow it. **`compare [VICTIM] [CONDITION] [--layout L] [--run DIR ...]`** shows one victim (default `a2a`), one condition (default `steady_a128k`, a campaign name or its code) and one layout (default `split-i`) with rows = sizes and columns = the 8 network states. **Runs** come from the attempts of `campaign_runs.tsv` in `$WORK` (else `$NPLB_WORK`, default `local/nplb-work`) whose status is `OK`, `WARN` or `FAIL` and that have a data dir; a relative data dir is looked up under `$DATA_ROOT` (default `data/nplb_h100_gpu`), as is a moved folder by its name. `--run DIR` (repeatable) reads a bare run folder instead of the log, as the anonymous state `unknown`, with layout and victim guessed from the folder name (else `?`). Plain text on stdout with a form feed between the pages (`PAGE_LINES`, `PAGE_COLS`, default 38 x 100); the verb pages it and saves the reports as `report` and `compare`. Exit 0, 2 for a usage error, an unknown name, a bad run log or a missing `--run` folder (message on stderr). The tests use the real October 2026 samples in `tests/fixtures/nplb/`. |
| `report_data.py` | The data side of `report.py`, stdlib only: reads a run folder (`config.json`, `<experiment>/data_app_<k>.csv`, `nplb_state.json`, `schedule_row.json`) and computes the numbers. Every metric uses the `Max-Duration_s` column, the slowest rank, because a collective ends when its last rank does: `t_med` is the median of the samples, `t_p99` the 99th percentile by nearest rank (the value at sorted position ceil(0.99 n)), `slowdown` = `t_med(condition) / t_med(alone)` of the same state, layout, victim and size. A cell is `FAIL` when its csv is missing in a run that finished, `n/a` when there is no usable `alone` to divide by, `-` when no run measured it; with several attempts of one cell the newest successful one (by `submitted`) wins. For `a2a`, `a2a_gbps` is the alone per-rank remote bandwidth, remote ranks x size x 8 / `t_med` / 1e9. The condition names and their short column codes (`s128k`, `b10/10`, ...) mirror `campaign.py`, and a test keeps them equal. |
| `report_tables.py` | The page layout of `report.py`: pure rendering to lists of lines, it reads no file. `summary_lines` is page 1 of `report` (verdict first), `group_table` one table per state, layout and victim, `compare_lines` the compare table, and `paginate` joins the blocks with form feeds so that no table is cut by the pager. A table too wide for the page is split into column groups that each repeat the fixed columns; a row is never cut. The summary page carries a legend of the column codes and of the cell values, the compare page one of the state codes (`n1a0s1`). |
| `diag_run.py` | Behind `nplb diag run [TARGET]`: why a run failed, read only (it writes nothing), stdlib only. **Target** as for `nplb follow` (a schedule row, a job id, a data dir path or a folder name under `$DATA_ROOT`; none = the newest submitted attempt of `campaign_runs.tsv`, else the newest attempt, else the newest folder). **Files**: reads `slurm_output.log` (the engine's `FAILED exit=N` lines give the exit codes; its last line says whether the run finished), `slurm_error.log` (its last 8 MiB), `<experiment>/error_app_N.log` (the first 256 KiB of at most 50), `config.json` (the `-msgsize` of a failed app) and `metadata.csv` in the parent of the data dir. **Report** (stdout: `#report diag-run-<folder>`, then the report; the verb pages it and saves it under that name), the verdict first: `VERDICT: FAIL <folder>: 3 failed apps in 2 of 4 experiments, <cause>` (`OK ... no failed app` when the log ends `Engine run finished` and no app failed, `UNKNOWN` when there is nothing to read: the missing files are named), then `cause:`, a one-line `note:` that the pattern is a guess, `next:` with the command to run, the table of failed apps (experiment, app, exit code or `?`, first error line; 10 rows then `... N more`), the distinct UCX / MPI / CUDA lines with repeat counts `xN` (timestamps, hosts and pids stripped, addresses ignored when comparing; 10 lines then `... N more`) and the last 8 lines of `slurm_error.log`. **Cause**: the first of memory registration (`ibv_reg_mr`, `Bad address`), GPU memory, network / UCX transport (`Transport retry count exceeded`, `Endpoint timeout`), crash (`Segmentation fault`, `signal 11`, `Bus error`), time limit (`DUE TO TIME LIMIT`, `TIMEOUT`) whose pattern matches the error logs, else `unclassified`; only the `ibv_reg_mr` pattern comes from a real log (October 2026), the others are guesses until a real log is seen. Env: `DATA_ROOT`, `WORK`, `PAGE_COLS`. Exit 0 for OK, 1 for FAIL and UNKNOWN, 2 for a target that cannot be found (message on stderr). |
| `pack.py` | Behind `nplb pack [--since TIME] [--out DIR] [--dry-run]`: puts what the slot produced into ONE `nplb-pack_<YYYYmmddTHHMMSS>.tar.gz` and prints how to copy it out (it never copies anything), stdlib only, Python 3.10. **In the tarball** (regular files only): `data/<run folder>/` (every folder a `data_dir` of a run log row names, plus every other folder under `$DATA_ROOT` whose newest mtime, the folder and its direct files, is not older than `--since`), `work/` (the whole `$WORK` folder: run log, net state, node map, `topology_*.tsv`, probe log, `reports/`, and `smoke/` with its own run log, reports and archive), `campaign/` (`$CAMPAIGN_DIR`), `reports/` (only when `$NPLB_REPORT_DIR` sits outside `$WORK`), `pack_manifest.txt` (`<sha256>  <size>  <path>` per file, the `sha256sum` format plus a size column) and `pack_info.json` (`created`, `since`, `crab_rev` and `blink_rev` as `{hash, dirty}` or null, `files`, `bytes`, `skipped`). **`--since`** is `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM[:SS]` (anything else exits 2); the default is the earliest `submitted` of `campaign_runs.tsv`, `smoke/campaign_runs.tsv` and `smoke/archive/*/campaign_runs.tsv`; with no rows, or a run log that cannot be read, nothing is cut. It only decides about folders no run log row names: a folder a row names is always packed. **Left out**: symlinks (never followed; counted and named under `skipped`), sockets and other non-regular files, `__pycache__`, `*.pyc`, and any earlier `nplb-pack_*` file or the pack folder itself. **Safe**: sources are only read; the tar is streamed and each sha256 is taken in the same pass; free space at the out folder must be at least the uncompressed bytes plus 5% (`VERDICT: FAIL pack: need X, free Y at DIR`, nothing written); the tarball is written as `.partial` and renamed, a failure removes it; `<name>.sha256` is written next to it (`sha256sum -c` format). `--dry-run` prints the same report (`VERDICT: DRY RUN`) and writes nothing. **Report** (stdout `#report pack`, then the report; the verb pages it and saves it as `pack`): the verdict first (`VERDICT: OK pack: N files, X MiB -> NAME (Y MiB)`), then path, size, file count, sha256, the CRAB and Blink revisions, what was skipped (5 lines then `... N more`) and the copy-out lines `scp <lab-host>:PATH .` and `sha256sum -c NAME.sha256`. Env: `DATA_ROOT`, `WORK` / `NPLB_WORK`, `CAMPAIGN_DIR`, `NPLB_REPORT_DIR`, `PACK_DIR` (default `local/nplb-pack`; `--out` wins), `PACK_FREE_BYTES` (replaces the free-space reading, for tests), `CRAB_DIR`, `BLINK_DIR` / `NPLB_LAB`, `NPLB_NOW`, `PAGE_COLS`. Exit 0 for OK and DRY RUN, 1 for FAIL (nothing to pack, not enough space, a write error), 2 for a usage error. |
| `rehearse.sh` | `rehearse.sh [--keep] [--help]`: the lab flow once on this laptop, with CRAB's `local` scheduler and a fake Blink, run it as `.crab-web-dev/bin/sandbox examples/nplb/rehearse.sh` (about 2 minutes; needs `mpirun` and the venv). Stages `setup` (a scratch folder `local/rehearsal-<YYYYmmdd-HHMMSS>/` that holds everything: the `local2` preset from "Running the laptop files", three launcher scripts named like the Blink programs, the state of CRAB's local scheduler, the work folder, the reports and the data root), `campaign` (`nplb campaign --local`, then `binary`, `datapath` and `NPLB_FAKE_CONTENTION=1` on the contended victims are written into the scratch copy of the blocks), `run` (`nplb run` for the first schedule row of each victim, each followed to its end; status OK or WARN, a data folder with `data_app_*.csv` and `schedule_row.json`, no app stopped by force), `follow`, `queue` (with `next`), `report` (`nplb report --all` must show a worst slowdown above 1), `compare`, `skip` (a skipped row is not proposed by `next`, `nplb follow` with no argument still follows the last real run, `skip --undo`), `pack` (`sha256sum -c`, `tar tzf` lists `pack_manifest.txt` and `work/campaign_runs.tsv`) and `pages` (every verb page and every page of every saved report is at most 38 lines of 100 columns). Narration `[HH:MM:SS] stage N/10: text` goes to stderr; stdout ends with `REHEARSAL OK` (exit 0) or `REHEARSAL FAILED at <stage>: <reason>` after the last 15 lines of that stage's output (exit 1, the scratch folder stays). On success the scratch folder is moved to `local/rehearsal-archive/` unless `--keep` is given; nothing is deleted and no process is signalled. One adapter: `crab run --json` names a local job `local-N`, which `nplb run` refuses (Slurm ids only), so `$CRAB` is a two-line script that strips `local-`; with no `squeue` or `scontrol`, `nplb follow` decides from the job log. `--source-only` defines the functions and returns (the tests use it). Exit 2 on an unknown flag. |
| `lab_start.sh` | `bash examples/nplb/lab_start.sh`: the first look at the lab in one command, typed after `cd <checkout> && git pull`; no argument. Steps (narration `[HH:MM:SS] step N/6: text` on stderr): go to the checkout and load `local/nplb_env.sh` (proxy exports) with `PYTHONPATH` unset; show branch, short HEAD and up to 5 tracked changes; `git fetch origin nplb` with a 60 s limit (`FAIL fetch: <first error line>; if it hangs, the proxy is not set: check local/nplb_env.sh`); refuse, never fix: a branch other than `nplb` (`FAIL branch is X, not nplb: run git checkout nplb`) or modified tracked files (listed, `FAIL local changes: send me this screen`) stop it at the summary, otherwise `git pull --ff-only` when behind origin/nplb (it says how many commits it brought), nothing when level, a WARN line when ahead; then `nplb build check` and `nplb node check` in that order, each printing its own page and pausing for Enter between pages only when stdin is a terminal. A failing check is remembered, not fatal. The last page (at most 38 lines of 100 columns) starts with `VERDICT: OK lab_start: branch nplb at <short>, build check/node check all OK` or `VERDICT: FAIL lab_start: <first failing step>`, then one OK/FAIL/WARN line per step; when all is OK it ends with `next: . examples/nplb/env.sh` and `next: nplb build blink` (the person types them: `env.sh` must be sourced in their own shell). Exit 0 for OK, 1 otherwise. Env: `NPLB_BIN` (the dispatcher, default `examples/nplb/nplb`; the tests use a stub). Does not build Blink, repair nodes or switch branches. |
| `fake_blink.py` | The fake Blink behind `rehearse.sh`, stdlib only, Python 3.10: `fake_blink.py PROGRAM [-msgsize S] [-iter N] [-warmup W] [-maxsamples M] [-grty G] [-gpu] [-endl] [-blength T] [-bpause T]` for `alltoall_comm_only`, `allgather_comm_only` and `allreduce_b`. With `-iter` it prints what `write_results` prints and the wrapper parser reads (a preamble line, the `-gpu` mode line, the header `Average,Minimum,Maximum,Median,MainRank`, `min(N, M)` rows, `Ran N+W iterations. Measured K iterations.`); with `-endl` (the aggressor) it prints nothing and runs until SIGTERM, which is how CRAB stops an `end: f` app, then exits 0; `-blength` and `-bpause` are accepted and ignored. Times are a model, not a measurement: `base + size x k` per program, a deterministic jitter of a few percent, times 1.6 when `NPLB_FAKE_CONTENTION=1`. Only rank 0 (`OMPI_COMM_WORLD_RANK`) prints; every rank sleeps `NPLB_FAKE_SLEEP` s (default 0.2); an aggressor gives up after `NPLB_FAKE_ENDL_MAX` s (default 600). No `-iter` and no `-endl`, an unknown program or flag, or a bad number exits 2 with a usage message. |
| `diag_counters.sh` | Behind `nplb diag counters snapshot [HOST...]` and `nplb diag counters diff [HOST...]`: did the NICs see ECN marks or CNPs while a job ran. Take a snapshot, run the job, take the diff. Both wrap `gpu_preflight.sh counters snapshot\|diff` (nothing is changed on a node; the only files written are the snapshot `$WORK/counters_snapshot.txt`, through `gpu_preflight.sh`, and the pager's report) and narrate live on stderr. The counters are a proxy: they show that the NICs saw ECN marks or CNPs, not the NPLB or AR setting of the switch, and the report says so. **`snapshot`** prints a short report saved as `diag-counters-snapshot`: `VERDICT: OK snapshot of N hosts saved`, or `VERDICT: WARN snapshot of K of M hosts` (exit 1) with the messages of `gpu_preflight.sh`. **`diff`** is saved as `diag-counters-diff`; its first line is the verdict: `VERDICT: OK ECN/CNP activity: yes (...)`, `... no (...)`, `VERDICT: WARN ECN/CNP activity: unknown (no ECN counter could be read)`, `VERDICT: WARN no counter moved (the snapshot may be too fresh or the job did not run)`, and `VERDICT: WARN unreachable: HOSTS, not in this diff; ...` (exit 1). Then `snapshot taken <age> ago` (from the file's modification time; `snapshot age unknown` when it lies in the future), a table per host (ECN marked, CNP sent, CNP handled: sums over the host's devices; ports with traffic), the list of ports with traffic, the proxy sentence, and from page 2 on every raw delta line `<host> <dev> <name> <delta>`. With no `HOST`, `gpu_preflight.sh` picks the nodes (diff: the hosts of the snapshot). No snapshot yet: `VERDICT: FAIL no snapshot: nplb diag counters snapshot` on stderr, exit 2, no report. Env: `WORK` (default `<checkout>/local/nplb-work`), `GPU_PREFLIGHT` (default `gpu_preflight.sh` next to this script), `DIAG_NOW` (epoch seconds that replace the clock for the snapshot age), `NPLB_REPORT_DIR`. Exit: 0 for OK and WARN of a complete run, 1 when a host was unreachable, 2 for bad usage, a missing or malformed snapshot or a missing `gpu_preflight.sh`. |
| `blink_build.sh` | Behind `nplb build blink [HOST...]` and `nplb build check [HOST...]`. `build` updates the GPU Blink checkout in `<lab>/blink-gpu` from `main` (a clone when it is missing), moves the old `build-cuda` to `<lab>/archive/build-cuda-<timestamp>` (never deletes it, because a CMake build folder keeps the MPI it was configured with), then configures and builds from scratch with the CUDA Open MPI's `mpicc`, logging to `<lab>/out/blink_build_<timestamp>.log`. `check` only reads. Both print a paged report whose first line is `VERDICT: OK` or `VERDICT: FAIL ...`, then one `OK`/`WARN`/`FAIL` line each for `ompi-cuda`, `ucx-cuda`, the CUDA folder, the Blink source (including whether the commit that widens buffer offsets to `size_t` is present), the three comm-only programs (they must contain the burst flag `-blength`) and, per node, whether `ldd` finds every library and `libmpi` comes from `ompi-cuda`. With no `HOST`, the nodes of the Slurm partition `PARTITION` (default `h100_4nodes`), else this node only. Environment: `NPLB_LAB` is the lab folder (default `$HOME/crab/lab`), `CUDA_ROOT` the CUDA install (default `/usr/local/cuda-12.3`), `JOBS` the parallel build jobs (default 16), `BLINK_REPO` the repository to clone (default `https://github.com/HLC-Lab/blink`), `SSH` the ssh command (default `ssh`). Exit 0 when nothing failed, 1 on a `FAIL`, 2 when a prerequisite is missing (nothing is changed then). |
| `node_tools.sh` | The entry point behind `nplb node check [HOST...]`, `nplb node diag HOST` and `nplb node fix [--sr V] [--ar V] [--rescan] [--links] [--peermem] [--nfs-head] [HOST...]` (V is 0 or 1); it holds the shared helpers, `check` and the verb switch, and sources `node_diag.sh`, `node_fix.sh` and `node_repair.sh`. All three narrate live on stderr and print one paged report (at most 38 lines x 100 columns) saved whole under their name (`node-check`, `node-diag`, `node-fix`). **`check`** is read only. It runs `gpu_preflight.sh` (its checks are not repeated here) and prints a first line `VERDICT: OK`, `VERDICT: OK, WARN n (...)` or `VERDICT: FAIL <host (checks)>`, then a grid with one row per node and one short column per check (`ok`, `WARN`, `FAIL`, or `--` when a check does not apply to that node: `nfs` is read on the workers only, `disk` on the head only), then one line per `WARN` or `FAIL` with its detail. A node that `gpu_preflight.sh` could not reach shows `FAIL` in every column. The extra `disk` check runs on the head node, the one that holds the CRAB checkout, with `df` on `<checkout>/data` (else the checkout): below `NODE_MIN_GIB` GiB free is `WARN`, below 5 GiB is `FAIL`. With no `HOST`, `gpu_preflight.sh` takes the nodes of the Slurm partition `PARTITION`. **`diag HOST`** is a read-only deep dive of one node. Its first line is `VERDICT: OK`, `VERDICT: HARDWARE (<why>)` (take the report to the lab staff: a cable, a card or a GPU), `VERDICT: SOFTWARE (<why>)` (the why says when `nplb node fix` repairs it) or `VERDICT: UNKNOWN (<why>)` (for example `cannot reach <host>`); then one `<OK, WARN or FAIL> <item>: <detail>` line per check and the last matching `dmesg` lines as evidence. The items: `ports` (`ibdev2netdev`, else `rdma link show`; says which answered; the 16 CX8 devices `mlx5_0` to `mlx5_5` and `mlx5_8` to `mlx5_17` must be `Up`, `mlx5_6` and `mlx5_7` are a disabled CX5 and ignored), `netdevs` (`ip -br link` for the netdevs of those ports), `cx8-pci` (the CX8 functions in `lspci -nn`: a line with `Mellanox`, `ConnectX` or `[15b3:` that is not a ConnectX-5, `15b3:1017`, `15b3:1019` or a virtual function; 16 expected), `gpus` (`nvidia-smi -L`, 8 expected), `speed` (`ethtool` speed of each `Up` netdev), `mst` (first lines of `mst status -v`), `dmesg` (lines with `Cable unplugged`, `NETDEV WATCHDOG`, `mlx5_core` with error, fail or timeout, or `Link down`), `nfs` (workers only: is `NFS_PATH` mounted; the head exports and mounts nothing) and `sr` (SR and AR of every active device through `sr_state.sh`). A missing tool is a `WARN` line, never a stop. The verdict is `HARDWARE` when `dmesg` has `Cable unplugged` lines and a port is not `Up` (the kernel log keeps old lines: with every port `Up` it is only a `WARN`), when `lspci` shows fewer than 16 CX8 functions, or when `nvidia-smi -L` lists fewer than 8 GPUs; `SOFTWARE` when ports are not `Up` although the cards are in `lspci` and `dmesg` has no cable line (a driver or link state, the lab's call), when `NFS_PATH` is not mounted on a worker, when `nvidia-smi -L` fails, or when SR or AR differ between devices; `UNKNOWN` when the node cannot be reached, or when ports are not `Up` and `lspci` could not count the cards. Every command on the node runs under `timeout 20`, through `ssh` or directly when `HOST` is this node, `localhost` or `HEAD`; nothing is written. **`fix`** changes things, so it prints its planned actions and asks `Proceed? [y/N]` first: it reads the answer from the terminal, `NPLB_YES=1` answers yes, and with no terminal and no `NPLB_YES` it prints `declined: nothing changed` and exits 1. Then, with `HOST...` the nodes to treat (default: the nodes of `PARTITION`, from `sinfo`; the head is `HEAD`, the others are the workers): (1) on the head it checks with `exportfs -v` that `NFS_PATH` is exported; it never edits `/etc/exports`: a missing export is a `FAIL` that prints the line for the lab to add, and no worker is mounted then; (2) on every worker it unmounts `NFS_PATH` while it is mounted and runs `mount -t nfs <head address>:<NFS_PATH> <NFS_PATH>` (the head address is the first IPv4 address of `getent hosts HEAD`), then reads the mount back with `findmnt`; (3) with `--sr V` and `--ar V` it runs `sr_set.sh` and `ar_set.sh` with that value on all the hosts (they read the value back; neither is touched without its flag). Four more repairs are opt-in flags, listed in the plan and covered by the same question; with several, they run in this order, before steps (1) to (3): `--rescan`, `--links`, `--peermem`, `--nfs-head` (see the `node_repair.sh` row). The report starts with `VERDICT: OK fix` or `VERDICT: FAIL fix (<failed steps>)`, then one line per step with its read-back. NFS mounts and the SR and AR settings do not survive a reboot of a node, which is what `fix` is for. Environment: `GPU_PREFLIGHT` is the preflight script (default `gpu_preflight.sh` next to this one), `NODE_MIN_GIB` the free-space warning level in GiB (default 20), `CRAB_DIR` the checkout (default: this one), `HEAD` the head node's name (default `uname -n`), `PARTITION` the Slurm partition (default `h100_4nodes`; also passed to the preflight), `SSH` the ssh command of `diag` and `fix` (default `ssh`; run with `-o BatchMode=yes -o ConnectTimeout=10`), `NFS_PATH` the folder the head exports and the workers mount (default: the parent of `CRAB_DIR`), `SR_STATE`, `SR_SET` and `AR_SET` the scripts `sr_state.sh`, `sr_set.sh` and `ar_set.sh` (default: next to this one), `NPLB_YES` (`1` answers yes to the `fix` question), `FIX_LINK_WAIT` and `FIX_RESCAN_WAIT` (seconds `--links` and `--rescan` wait before reading again, default 10 and 3), `PCI_RESCAN_PATH` (the file `--rescan` writes, default `/sys/bus/pci/rescan`), `NPLB_REPORT_DIR` the pager's folder. Exit: `check` 0 when nothing failed, 1 on a `FAIL`, 2 on bad usage or a missing preflight script; `diag` 0 on `OK`, 1 on any other verdict, 2 on bad usage; `fix` 0 when every step read back fine, 1 on a failed step or when declined, 2 on bad usage or no `HOST`. |
| `blink_try.sh` | Behind `nplb build try [HOST HOST]`: one real Blink `alltoall_comm_only` run by hand through the lab's CUDA-aware `mpirun` on two nodes, the way a failed smoke app hides. Cases: default, `-gpu`, one per UCX transport (`TRY_TLS`), one per UCX override (`TRY_ENVS`), the smoke layout of 16 ranks with one NIC each, and each NIC alone (`TRY_NICS`; set `TRY_TLS=`, `TRY_ENVS=` and `TRY_NICS=` empty for a 1.5 minute check of the first two). Each run has a time limit (`TRY_TIMEOUT`, 90 s); the report (`build-try`) gives its exit code and the first and last output lines. |
| `net_ping.sh` | Behind `nplb net ping HOST_A HOST_B`: pings, NIC by NIC, the address of the same netdev on the other node from that netdev, then once more with a `PING_BIG` byte (default 4096) packet that must not be fragmented. Report `net-ping`: `OK`, `FAIL` or `FAIL-BIG` per NIC. |
| `net_rdma.sh` | Behind `nplb net rdma HOST_A HOST_B`: RC (`ib_write_bw`) and UD (`ib_send_bw -c UD`) perftest runs per NIC (`RDMA_NICS`) between two nodes, without UCX or MPI. A UD run counts only when the receiving node reports a rate (a UD sender reports one even when nothing arrives); on the first lab run this check failed on a pair that works, so treat the UD rows as unproven. Report `net-rdma`. |
| `switch_dump.sh` | Behind `nplb net dump [SWITCH...]` and `nplb net diff [WORD]`: saves `display current-configuration` of the lab switches (SSH session, one password prompt per switch, no host-key check) as `<WORK>/switch_cfg/<name>.cfg`, then runs `switch_diff.py`. |
| `switch_diff.py` | `switch_diff.py DIR [--odd Leaf3,Leaf4] [--show WORD]`: compares the config lines of the odd leaves with those of the other leaves after turning every digit into `N` (so a pure number difference is not seen), lists what is missing or extra, and with `--show` prints the lines around WORD (lines with `password` or `cipher` are never printed). Full lists go to `DIR/diff.txt`. |
| `batch_run.sh` | Behind `nplb batch start [--repeat K] ROW...` (N or A-B; `--repeat K` runs the list K times, rounds after the first with `--force`), `nplb batch status` and `nplb batch stop`: runs `nplb run N` for each row, one at a time, in a background runner (setsid + nohup) that survives a logout; a refused or failed row is logged and the next one starts; `nplb report` at the end. Log, per-row table and pid file in `$WORK/batch`. Report `batch-status`. |
| `node_procs.sh` | Behind `nplb node procs [HOST...]` (list) and `nplb node stop [HOST...]` (SIGTERM, then SIGKILL after 3 s, after `Stop N processes? [y/N]`): the Blink, perftest, `mpirun` and `orted` processes left on the nodes, which keep sending traffic. Report `node-procs`. |
| `node_scan.sh` | Behind `nplb node scan [HOST...]`: every Slurm node of every partition (`sinfo -a`), asked over ssh in parallel for GPUs, ACTIVE RDMA ports, the `/root/crab/CRAB` checkout and load, to find spare nodes beyond the campaign partition. Read-only. Report `node-scan`. |
| `node_diag.sh` | Not run on its own: sourced by `node_tools.sh`. Holds the `diag` code (`cmd_diag`, one collector per check, the verdict rules); what `diag` prints is described in the `node_tools.sh` row. |
| `node_fix.sh` | Not run on its own: sourced by `node_tools.sh`. Holds the `fix` code (`cmd_fix`: the plan and question, the export check, the NFS remount, the SR and AR steps); what `fix` does is described in the `node_tools.sh` row. |
| `node_repair.sh` | Not run on its own: sourced by `node_tools.sh`. The four opt-in repairs of `nplb node fix`, each read-then-repair-then-read-back, per host, touching only what is wrong; a failed read-back is a `FAIL` line and a failed step of the verdict. **`--rescan`**: on a node whose `lspci -nn` shows fewer than 16 CX8 functions it runs `sh -c 'echo 1 > /sys/bus/pci/rescan'`, waits `FIX_RESCAN_WAIT` seconds and counts again (`OK <host> rescan: count now 16/16 CX8 functions`, or `FAIL ... still N of 16 CX8 functions: the card is not seen by the machine, a hardware call for the lab`; a rescan is harmless on a running node, a missing card usually stays missing). **`--links`**: for each of the 16 RDMA ports that `ibdev2netdev` (else `rdma link show`) lists as not Up it runs `ip link set <netdev> up`, waits up to `FIX_LINK_WAIT` seconds and reads the ports again (`OK <host> links: mlx5_4 is Up now`, or `FAIL ... still not Up: no signal from the far end: cable or switch port, a hardware call (run nplb node diag <host>)`); ports that are Up are not touched. **`--peermem`**: on a node whose `lsmod` lacks `nvidia_peermem` it runs `modprobe nvidia_peermem` and reads `lsmod` again; a failed `modprobe` shows its first stderr line. **`--nfs-head`**: on `HEAD` only, when `exportfs -v` does not list `NFS_PATH`: `systemctl restart nfs-server` (else `service nfs-kernel-server restart`), `exportfs -ra`, then `exportfs -v` must list it; if it still does not, the `FAIL` is followed by the line the lab adds to `/etc/exports` (`<NFS_PATH> *(rw,sync,no_root_squash,no_subtree_check)`, then `exportfs -ra`): `/etc/exports` is never written. `nplb node diag` names `--links` and `--rescan` in its `next:` line for the matching faults, and `nplb node check` prints a `hint` line for a `peermem` problem and for an `nfs` `FAIL`. |
| `pages.py` | The report pager behind `nplb page` and `nplb reports`: shows a report in numbered pages of at most 38 lines x 100 columns (`emit NAME` saves the report read from stdin and prints page 1, `page [N\|next\|prev] [REPORT]` replays a page, `reports` lists the saved ones). Reports are saved whole in `local/nplb-work/reports`. Environment: `PAGE_LINES` and `PAGE_COLS` change the page size (defaults 38 and 100), `NPLB_REPORT_DIR` sets the reports folder, `NPLB_WORK` moves the whole work folder (reports go in its `reports` folder). The bash wrapper is in [`lib/`](lib/). |
| `gpu_lab.sh` | Lab session for the H100 GPU suites in steps that each print one screen (38 lines x 100 columns) and keep the full detail in a log; `status` shows the checkout, the preset, `crab validate` of the three GPU configs and a node x check grid and is read only; `smoke` submits the smoke config with a plain `crab run`, waits for the job and shows whether it ran, what the victim measured, what the full suites will cost and what the NICs saw; see [below](#one-screen-lab-session). |
| `gpu_lab_run.sh` | Sourced by `gpu_lab.sh`: the `smoke` step (submits the smoke config with a plain `crab run`, waits for the job, then shows what ran, what the victim measured, what the full suites will cost and the NIC counters before and after), with the embedded python that reads the finished run. |
| `gpu_lab_dual.sh` | Sourced by `gpu_lab.sh`: the `dual-port` step (Gb/s of one H100 card with one port and with both ports from GPU memory, through UCX and raw, and what the NIC counters saw) and the `salloc` wrapper the steps that need two nodes share; see [below](#one-screen-lab-session). |
| `topology.sh` | Reports each RDMA port of every node over ssh and listens for LLDP to name the leaf switch behind it (round 2, run by `bringup.sh topo`). |
| `topology_summary.py` | Turns the TSV of `topology.sh` into a one-screen grid and verdict, as `topology.sh` does at its end. |
| `topo_tools.sh` | The entry point behind `nplb topo [--from TSV] [HOST...]`: runs `topology.sh` (LLDP probe, about `LLDP_WAIT` seconds, 40 by default), keeps the probe's own screen in `$WORK/topo_probe.log`, takes the newest `$WORK/topology_<time>.tsv`, builds `$WORK/node_map.json` with `topo_map.py` and prints one paged report (at most 38 lines x 100 columns, saved whole as `topo`) whose first line is `VERDICT: OK ...` or `VERDICT: WARN ...`. It narrates on stderr as `[HH:MM:SS] text`. `--from TSV` skips the probe and reads an earlier TSV (no ssh, no `tcpdump`). Without `HOST` the probe takes the nodes of `PARTITION`. Exit 0 when the map was built (OK or WARN), 1 when the probe failed (exit 1) and no leaf is known, 2 on bad usage or a probe that could not run. Environment: `TOPOLOGY_SH` the probe (default `topology.sh` next to this one), `WORK` (default `local/nplb-work`), `PARTITION` (default `h100_4nodes`), `LLDP_WAIT` (seconds to wait for one LLDP frame per netdev, passed to the probe), `NODE_LEAF` (manual leaves, see `topo_map.py`), `NPLB_REPORT_DIR` the pager's folder. |
| `topo_map.py` | Behind `nplb topo`: `topo_map.py build --tsv FILE [--hosts H,...] [--out PATH] [--notes TEXT] [--probe-rc N]` reads the TSV of `topology.sh` and writes the node-to-leaf map (JSON, default `$WORK/node_map.json`) and prints the report. A node's leaf is the set of switches heard over LLDP on its active ports (a node can hang on two leaves of a pod; down ports and ports that heard nothing add nothing), or `null` when none was heard. `NODE_LEAF` (`host=leaf,host=leaf`, several leaves of one host joined by `+`) and the file `$WORK/node_leaf.txt` (one `host leaf` per line, `#` starts a comment) replace what LLDP gave for the hosts they name; the env var wins over the file. The nodes are taken in the order Slurm hands them to a job (sorted by name, numbers in natural order), which no layout changes, so the report previews, for each layout, which nodes the victim and the aggressor get: `split-i` (CRAB `interleaved` partitions of share 50) puts the victim on nodes 0 and 2 and the aggressor on 1 and 3 of the four; `split-l` (`linear`) puts the victim on nodes 0 and 1 and the aggressor on 2 and 3; `shared` gives both all the nodes. Each side's pair is `same` (identical leaf sets), `partial` (some leaf in common), `cross` (none in common) or `unknown` (a leaf is not known), and `shared_leaves` lists the leaves that host both sides. The map's `source` is `lldp+slurm-order`, `manual` (every leaf from an override), `lldp+manual+slurm-order` or `slurm-order`. `--probe-rc` is the probe's exit status (shown in the verdict). Only LLDP frames in the format `topology.sh` parses are read; a switch whose name has no `Leaf<N>` is labelled by its first 8 characters. |
| `net_tools.sh` | The entry point behind `nplb net declare\|set\|show\|sheet\|record`, which keep the network state of the lab: three independent switches, NPLB (on/off) and AR = adaptive routing (on/off), both set by hand on the switch CLI, and SR = selective repeat (on/off), a per-node NIC setting; the NIC has its own adaptive-routing bit too, called AR(nic) here. Nothing on the nodes can read the switches, so the switch state is what you declared and every report says so. **`declare nplb=on\|off ar=on\|off`** writes `$WORK/net_state.json` (`nplb`, `ar`, `declared_at`, `label_switch` such as `nplb1_ar0`, and `"together": true` when both are on, which is untested on these switches); the first declaration also writes `net_state_initial.json`, which is never overwritten. **`set sr=on\|off [arnic=on\|off] [HOST...]`** runs `sr_set.sh` (and `ar_set.sh` only with `arnic=`) on all the hosts, each reads its value back, and the paged report (saved as `net-set`) starts with `VERDICT: OK SR=1 AR(nic)=0 on 2 nodes` or `VERDICT: FAIL <bit> not set (exit N)`; both bits are tried even when the first fails. **`show [HOST...]`** reads SR and the NIC AR bit of every node with `sr_state.sh` and prints a paged report (saved as `net-show`): `VERDICT: OK nplb=on ar=off sr=1 arnic=0`, `VERDICT: WARN no switch state declared (...)` or `VERDICT: FAIL` with `SR mixed (<hosts>)`, `AR(nic) mixed (<hosts>)`, `AR(nic) unreadable (<hosts>)` or `<hosts> unreadable`; then the declaration with its age (`age unknown` when the timestamp is in the future or unparsable) and a table host x SR x AR(nic) x devices. **`sheet STATE`** prints the switch commands to paste for a state (`nplb=on ar=off`, also comma separated, or the aliases `base`, `nplb`, `ar`, `nplb+ar`), saved as `net-sheet`; `sheet --restore` does the same for `net_state_initial.json`. NPLB off is the sequence of the Leaf1 session (a Huawei CE switch refuses `undo mode2 enable` while the self-heal id is configured): `sys`, `packet event monitor`, `packet-loss self-heal`, `undo id 1001 match nplb-er mode2 qos-local-id 13`, `commit`, `q`, `q`, `nplb-er mode mode2`, `undo mode2 enable`, `commit`, `display this`. NPLB on is `sys`, `nplb-er mode mode2`, `mode2 enable`, `commit`, then the restore of the id: `packet event monitor`, `packet-loss self-heal`, `id 1001 match nplb-er mode2 qos-local-id 13`, `commit`, `q`, `q`, `display this`, with a note to ignore an "already configured" answer on the id line. A line on every sheet says the NPLB commands come from the Leaf1 screenshots and should be checked on one other switch first. The AR switch command is not known, so the AR group prints a discovery block instead of a command: `display current-configuration \| include load-balance\|dlb\|adaptive`, `load-balance ?` and `display load-balance ?`, to paste on one switch and send back. The sheet ends with the `nplb net declare ...` line to run afterwards, and `nplb+ar` is marked as the last state to try. **`record DIR [HOST...]`** writes `DIR/nplb_state.json` and prints `recorded: <label> -> <path>`; it exits 1 without writing when no switch state is declared, or when SR or AR(nic) differ between nodes or a node cannot be read, and 2 when `DIR` does not exist. With no `HOST`, the nodes of `PARTITION` (from `sinfo`). Steps narrate on stderr as `[HH:MM:SS] text`. Exit 0 on success (`show`: OK or WARN), 1 on a `FAIL` verdict or a refusal, 2 on bad usage. Environment: `WORK` (default `local/nplb-work`), `NET_SWITCHES` (space-separated switch names printed in the sheet header; unset prints a hint to set it in `local/nplb_env.sh`), `SR_STATE`, `SR_SET` and `AR_SET` (default: `sr_state.sh`, `sr_set.sh`, `ar_set.sh` next to this one), `CRAB_DIR` (default: this checkout), `BLINK_DIR` (default `$NPLB_LAB/blink-gpu`), `NPLB_LAB` (default `$HOME/crab/lab`), `PARTITION` (default `h100_4nodes`), `NPLB_REPORT_DIR` the pager's folder. |
| `net_state.py` | Behind `nplb net`: the state logic, stdlib only. Subcommands `declare`, `label`, `sheet`, `nic` (turns the lines of `sr_state.sh` into JSON), `show-report`, `declared` and `record`. `label(nplb, ar, sr)` is importable and returns the label of one of the 8 network states, such as `nplb1_ar0_sr1` (values 0/1 or on/off); `net_state.py label nplb=1 ar=0 sr=1` prints it. The record `DIR/nplb_state.json` has `switch` (`nplb`, `ar`, `declared_at`, `together`), `nic` (`sr`, `ar` and `hosts`: per host `sr` and `ar`, each 0 or 1), `label`, `crab_rev` and `blink_rev` (`hash`, the short git hash, and `dirty`, true when tracked files are modified; `blink_rev` is `null` when the Blink folder is absent, and a folder that is not a git checkout gives hash `unknown`) and `recorded_at`. JSON files are written through a temp file and a rename. `WORK` is `$WORK`, else `$NPLB_WORK`, else `local/nplb-work`; `$NPLB_NOW` (`YYYY-MM-DDTHH:MM:SS`) replaces the clock, for tests. |
| `pingpong_set.py` | Builds the ping-pong ceiling configs for `make_configs.py --set pingpong` (see [round 2](#round-2-topology-and-ping-pong-ceilings)). |
| `diagnose.sh` | Tries mpirun binding flags and `nic_check.sh` on NIC subsets and node pairs to find where the per-node bandwidth cap comes from; see [below](#diagnose-binding-and-the-bandwidth-cap). |
| `analyze.py` | Reads CRAB data directories and writes `summary.csv` and `summary.md`. |
| `plot.py` | Draws PNG charts from `summary.csv` (needs matplotlib). |

The scripts need Python 3.10 or newer. `probe.py` and `make_configs.py` use only the standard
library; `analyze.py` needs pandas (CRAB's `.venv` has it); `plot.py` also needs matplotlib.

## The network configurations

The network team sets these outside CRAB. Nothing inside a run can see which one is active, so log
it for every job (see [Run log](#run-log)).

| ID | Routing | NIC selective repeat | Runs |
|----|---------|----------------------|------|
| C0 | ECMP (NPLB off) | off | everything |
| C2 | NPLB | on | everything |
| C3 | NPLB | off | phase 1 only, one round, 1 path (sanity check: expected to degrade) |

The path count `P` is `UCX_IB_NUM_PATHS`. The generator writes it into every app's `env`, so `P1`
and `P4` configs differ only there. With one path, each rank pair is one flow, which ECMP hashes
onto one spine link; with four, the pair's traffic is split over four independently hashed flows.

## 1. Install

On the cluster, from your home or work directory:

```bash
git clone https://github.com/HLC-Lab/CRAB
cd CRAB
git checkout nplb
make            # virtual environment, wrappers, then the setup wizard
```

In the wizard, install Blink (the build needs CMake ≥ 3.16, a C/C++ compiler and an MPI). If
CRAB is already installed, `git fetch && git checkout nplb && git pull` and run `crab setup` only
if Blink is missing. Check the Blink build has the programs the configs use:

```bash
BLINK=$(.venv/bin/python -c "import crab.setup.memory as m; print(m.get_receipt('blink')['binary_path'])")
ls "$BLINK" | grep -E '^(pingpong_b|pairwise_nb|alltoall_nb|allgather_nb|incast_nb|alltoall_man|alltoall_comm_only|allgather_comm_only|allreduce_nb)$'
```

All nine names must be listed. If the folder only has older names (`o2o_nb`, `a2a_nb`, ...), the
receipt points at an old Blink build: run `crab setup` again and rebuild Blink.

## 2. Preset

CRAB ships no preset for the NPLB clusters: you put one in your overrides folder (see
`docs/using/presets.md#your-overrides`). It is any `*.json` file there, and a file may hold several
presets. In a checkout the folder is `local/presets/` (git-ignored); in a pip install it is
`~/.config/crab/presets/`; `crab info` prints the one in use on its `overrides:` line.

### The H100 test cluster

`examples/nplb/presets/nplb_h100.json` is the complete preset of the 4-node H100 test cluster
(partition `h100_4nodes`, Open MPI's own `mpirun` over UCX, see
[below](#clusters-where-srun-cannot-start-mpi)). Copy it into the overrides folder, then check
that CRAB reads it:

```bash
mkdir -p local/presets                            # make the overrides folder (it does not exist yet)
cp examples/nplb/presets/nplb_h100.json local/presets/   # the preset is now found by name: -p nplb_h100
.venv/bin/crab presets show nplb_h100             # the preset as CRAB will use it, and the file it came from
```

`bringup.sh` runs its jobs with `-p nplb_h100`, so keep that name for this cluster. For another
cluster, copy the file under a new file name, rename the preset in it and change the partition
(and the launcher advice below), for example `local/presets/mycluster.json`.

### Another cluster

A starting point for a Slurm cluster with Open MPI built with PMIx, as `local/presets/mycluster.json`:

```json
{
  "mycluster": {
    "description": "NPLB test cluster",
    "scheduler": "slurm",
    "launcher": "srun",
    "launchers": { "srun": { "flags": ["--mpi=pmix"] } },
    "env": {},
    "sbatch": ["--account=<account>", "--partition=<partition>"],
    "header": ["module purge", "module load <the modules Blink was built with>"]
  }
}
```

Leave `--cpu-bind` out of the flags: the configs set `cpus`, and CRAB binds those apps itself (it
warns at submit if it has to drop a `--cpu-bind`). `srun --mpi=list` shows which PMI plugins the
cluster offers.

### Clusters where srun cannot start MPI

If `srun --mpi=list` offers no PMI plugin that the cluster's Open MPI was built with (no `pmix`),
launch with Open MPI's own `mpirun` instead; it still runs inside the Slurm allocation:

```json
{
  "mycluster": {
    "description": "NPLB test cluster, mpirun",
    "scheduler": "slurm",
    "launcher": "mpirun",
    "launchers": {
      "mpirun": {
        "dialect": "openmpi4",
        "flags": ["--mca", "pml", "ucx", "--mca", "btl", "^openib"]
      }
    },
    "env": {},
    "sbatch": ["--partition=<partition>"],
    "header": []
  }
}
```

CRAB passes the app's `cpus` as `--cpu-list <cores> --bind-to cpu-list:ordered`, which binds
local rank *i* to the *i*-th core (with Open MPI 4.1, `--cpu-set` is only an envelope and leaves
the ranks unbound). Leave `--map-by` and `--bind-to` out of the preset flags. Add
`--allow-run-as-root` when the jobs run as root.

`mpirun` reads every program and every per-rank script from the same path on every node, so the
CRAB checkout (its `benchmarks/` and `data/` folders included) must be on a filesystem all nodes
share. Without one, export the checkout's parent folder from the node that holds it over NFS and
mount it at the same path on the others. Then check every node from that node:

```bash
examples/nplb/preflight.sh <node1> <node2> <node3> <node4>   # or: PARTITION=<partition> examples/nplb/preflight.sh
```

It prints one `OK`, `WARN` or `FAIL` line per check and node and exits 1 on any `FAIL`. It checks
that the checkout is there and NFS-mounted (exported, on the node you run it from), that `data/`
is writable, that the nine Blink programs are there, and that `mpirun`, the `libmpi` that Blink
loads, `ompi_info`, `ucx_info` and CRAB's Python are the same as on the node you run it from. The
`route` line shows which network interface reaches that node: a `WARN` naming an RDMA device
means the NFS traffic shares a link with the measurements.

## 3. Probe the nodes

Run the probe once per node, on the nodes the campaign will use, and merge the lines:

```bash
srun -N4 --ntasks-per-node=1 --exclusive -A <account> -p <partition> -t 00:05:00 \
     .venv/bin/python examples/nplb/probe.py --json > probe.jsonl
.venv/bin/python examples/nplb/probe.py --merge probe.jsonl --out profile.json
```

The merge prints a table and a list of warnings, and writes `profile.json`:

- `nodes`: the hostnames in Slurm's order. The configs assume the order Server1, Server2, Server3,
  Server4 (partitions are dealt in this order). If Slurm orders them differently, pin the order
  with `--nodelist` in the preset's `sbatch` or ask the admins.
- `nics` and `cpus`: one active port per rank and one core next to each card, already ordered so
  that rank *i* gets `nics[i]` and the *i*-th core (CRAB sorts `cpus`, so the NICs follow the
  cores).
- `ppn`: the number of active ports, which becomes the ranks per node.
- `mem_gib`: the smallest node memory, used to skip message sizes whose buffers would not fit.

Read every warning before going on. In particular:

- **A device with two active ports** may be one dual-port NIC exposed as two devices: then one
  device per rank uses only half the card. Report it before running the campaign.
- **Link rate** per port: record it in the run log. It sets the expected bandwidth ceilings.
- **Link layer** must be `Ethernet` (RoCE) on the NPLB fabric.

When each card shows up as two devices (two PCI functions of one slot, such as `mlx5_0` and
`mlx5_1` on `0000:19:00.0` and `0000:19:00.1`), the default profile has one rank per port. For
one rank per card, merge again with `--per-card`: it keeps the lowest active port of each card
(the device with the lowest PCI function that has one) and pairs the cores the same way:

```bash
.venv/bin/python examples/nplb/probe.py --merge probe.jsonl --out profile16.json
.venv/bin/python examples/nplb/probe.py --merge probe.jsonl --per-card --out profile8.json
```

Which port of a card faces which switch is not visible from the node: ask the network team
before comparing per-card results with per-port ones.

### Read the NIC side of the network configuration

```bash
examples/nplb/sr_state.sh <node1> <node2> <node3> <node4>
```

For every active device of every node it reads the `ROCE_ACCL` register with `mlxreg` and prints
Selective Repeat (`SR`) and adaptive routing (`AR`). It exits 0 only when every device was read
and all SR values agree. It never changes them. Record the result in the run log; the switch side
(NPLB on or off) has to be read on the switches.

`sr_set.sh` changes the setting:

```bash
examples/nplb/sr_set.sh 1 <node1> <node2> <node3> <node4>    # 1 turns SR on, 0 turns it off
```

On each node it writes the selective repeat field of `ROCE_ACCL` on every device with an active
port, reads it back and prints `OK` or `FAIL` per device. It exits 0 only when every device reads
back the value. Use it to put the NICs in the state a network configuration needs (C2 has SR on,
C0 and C3 have it off). The change is lost at reboot, so run `sr_state.sh` afterwards and again
before a campaign.

The probe cannot see which leaf each NIC is cabled to. Get the NIC-to-leaf wiring from the
network team and record it; the 3-node layout and `p0_local` depend on it.

### Check that each rank uses its own NIC

On a new cluster, run this before generating configs. If the ranks of a node end up sharing one
card, every phase-2 slowdown reads 1.00, because each node is already capped before the aggressor
starts. In a 2-node allocation:

```bash
salloc -N2 --exclusive -A <account> -p <partition> -t 00:15:00
BLINK=$(.venv/bin/python -c "import crab.setup.memory as m; print(m.get_receipt('blink')['binary_path'])")
SRUN_FLAGS="--mpi=pmix" examples/nplb/nic_check.sh profile.json "$BLINK"
```

Load the modules Blink was built with first, as in the preset's `header`. `SRUN_FLAGS` takes the
preset's launcher flags (leave it out if it has none); `MSG` and `ITER` change the message size
(default 2 MiB) and the iterations (default 200). The script runs Blink's `pairwise_nb` on the two
nodes in four placements (1 rank per node; all ranks with one NIC each; all ranks on the first
NIC; the same with Open MPI forced onto UCX). For each it prints the per-rank bandwidth and the
GB each NIC sent, read from the nodes' RDMA counters (`port_xmit_data`).

Good: `per-rank` is close to `one-rank` (about one link's rate per rank) and its bytes are spread
evenly over every NIC. Bad: `per-rank` is close to `first-nic`, about `one-rank` divided by the
ranks per node, which means the ranks share one card's bandwidth whatever `UCX_NET_DEVICES` says;
the counters then show where the bytes went. A `per-rank-ucx` that fails or differs from `per-rank`
is a finding too: note it.

If `per-rank` is not close to `one-rank`, stop: phase 2 would show slowdown 1.00 because each node
is capped. A counter shown as `n/a` is not readable on that node; the bandwidth is still valid.

On a cluster that launches with `mpirun` (see the preset section), run it with:

```bash
LAUNCHER=mpirun MPIRUN_FLAGS="--mca pml ucx --mca btl ^openib" \
    examples/nplb/nic_check.sh profile.json "$BLINK"
```

(add `--allow-run-as-root` to `MPIRUN_FLAGS` when running as root). The ranks then start with
`mpirun -H <the two allocated hosts> --cpu-list <the profile's cores> --bind-to cpu-list:ordered`,
the same way CRAB launches them; the NIC counters are still read with plain `srun`. There are
three cases (no `per-rank-ucx`: the flags already choose UCX), each followed by `unbound ranks: N`
from mpirun's `--report-bindings`, and `per-rank` also lists the binding of every rank. Good: 0
unbound ranks, and local rank *i* bound to the *i*-th core of the profile's `cpus`.

## 4. Generate the configs

```bash
.venv/bin/python examples/nplb/make_configs.py profile.json --out configs/
```

Defaults: both test sets, C0 and C2 with 1 and 4 paths, plus C3 for phase 1 with 1 path. The
summary lists every file, its experiments, the message sizes skipped for memory, and the
assumptions it made. Useful flags: `--set md|colleague|both`, `--net C0 C2`, `--paths 1`,
`--minruns`, `--maxruns`, `--walltime`, `--timeout`, `--mem-gib`, `--iter-small`,
`--iter-128m`, `--iter-512m`. `--smoke` writes a short version (3 sizes, 50 iterations, one run)
to test the whole chain. `--aggressors a2a burst incast` limits phase 2 of the md set to those
aggressors (`burst` is the whole burst grid; the 3-node layout only has `pw`), for example
`--aggressors a2a` for the victims alone and against a steady all-to-all.

`--tag T` appends `_T` to every file name, config name and job name (experiment names do not
change), so two variants of the same campaign stay apart, such as `--tag ppn8` from
`profile8.json` and `--tag ppn16` from `profile16.json`. A tag has letters, digits and `-` only.

In the md set, every app except the aggressors times `-grty G` operations per sample, with
`G = min(100, ceil(2 MiB / message size))`: 100 up to 4 KiB, 64 at 32 KiB, 8 at 256 KiB, 1 from
2 MiB. Blink synchronizes the ranks before every timed sample, so with one small operation per
sample the times mostly measure how far apart the ranks start. The colleague set keeps its own
arguments.

The layout follows the node count in the profile: 4 nodes use partitions Server1+Server3 (victim)
and Server2+Server4 (aggressor); 3 nodes use Server1+Server3 (victim) and Server2 (aggressor).

## 5. Run

One config is one Slurm job. Run the files of the network configuration that is active now, for
example C0:

```bash
for f in configs/md_*_C0_P1.json configs/col_*_C0_P1.json; do
    .venv/bin/crab run -p mycluster "$f"
done
```

Change `UCX_IB_NUM_PATHS` rounds by running the `_P4` files. When the network team switches to
C2, run the `_C2_` files. Log each job (see [Run log](#run-log)).

Results go to `data/<preset>/<config name>_<timestamp>/`.

### Check the first job

In the job's data directory:

- `crab_run.json` → `launches`: each app's launch line, `ppn`, `env` (with `UCX_IB_NUM_PATHS`
  and `UCX_MAX_RNDV_RAILS`) and `nics`.
- `<experiment>/bindings_app_<id>.log`: one line per rank, such as
  `crab: <host> local rank 3 -> UCX_NET_DEVICES=mlx5_3:1`, and srun's CPU masks
  (`cpu-bind=MAP ... mask 0x...`). Every local rank should name a different card and a core next
  to it.

## 6. Analyze and plot

```bash
.venv/bin/python examples/nplb/analyze.py data/mycluster/md_* data/mycluster/col_* --out results/
```

With tagged configs, analyze one tag at a time (`data/mycluster/md_*_ppn8_*` into one `--out`,
`*_ppn16_*` into another): the C0-vs-C2 tables pair rows by experiment, not by file, so two
variants in one call would be mixed.

`results/summary.csv` has one row per data directory, experiment and message size;
`results/summary.md` has the tables, including **C0 vs C2** side by side. The metrics:

| Column | Meaning |
|--------|---------|
| `bw_gbps` | per-rank bandwidth: message size × granularity / median time; for pingpong, one-way, from rank 0's round trip / 2 (Blink's `MainRank` column) |
| `spread_med`, `spread_p99` | max / min time across ranks, per iteration (median and p99): the collision signal. `–` for pingpong. At small sizes it mostly measures how far apart the ranks enter each timed window (Blink runs an `MPI_Allreduce` before every window), so read it at large sizes |
| `run_cv` | coefficient of variation of the per-run medians: ECMP hash luck |
| `slowdown_med`, `slowdown_p99` | victim time with the aggressor / victim time alone, same file and size |

The plots need matplotlib, which is not part of CRAB. Use a separate environment, on the cluster or
after copying `summary.csv` to another machine:

```bash
python3 -m venv ~/nplb-plot && ~/nplb-plot/bin/pip install matplotlib pandas
~/nplb-plot/bin/python examples/nplb/plot.py results/summary.csv --out results/plots/
```

It writes `bw_*.png` and `spread_*.png` per test without an aggressor, and `slowdown_*.png` with
one panel per aggressor, C0 and C2 on the same axes.

## Round 2: topology and ping-pong ceilings

Two stages, one command each, run on the node that holds the CRAB checkout. Both read
`profile8.json` (per card) and `profile16.json` (every port) from `PROFILE_DIR` (the checkout by
default), written in [section 3](#3-probe-the-nodes). `bringup.sh` sources `env.sh`, echoes every
command before it runs and stops at the first failure.

```bash
examples/nplb/bringup.sh topo           # the topology probe
examples/nplb/bringup.sh pingpong       # generate and submit the 4 ping-pong jobs
examples/nplb/bringup.sh analyze pingpong   # when the jobs are done
examples/nplb/bringup.sh pw 4 8         # node total with 4 and with 8 ranks per node, one long run
examples/nplb/bringup.sh analyze pw     # when those jobs are done
```

**`topo [HOST...]`** runs `topology.sh`: over ssh, every node reports each RDMA port (state,
speed, netdev, bond, PCI, NUMA) and listens for one LLDP frame per netdev, which names the leaf
switch and port behind it. Without a HOST it lists the nodes of `PARTITION`. It changes nothing
on the nodes or the switches. It takes about `LLDP_WAIT` (40 s by default) plus the ssh round
trips, around a minute, and prints a summary of at most 40 lines; the raw lines go to
`$WORK/topology_<time>.tsv`. It exits with the code of `topology.sh` (0 all nodes answered, 1 a
node failed, 2 setup missing).

A good result: every port has a speed and a `leaf:port`, every node is named in a pod, and the
victim pair verdict reads `YES` (the victim pair crosses the spine). If it says `NO` or
`UNKNOWN`, photograph the summary and stop before the a2a round: the node order or the layout
must change. If LLDP is not visible on the hosts, the summary says so: the leaf wiring then has to
come from the switch reader (a later phase), and the rest of the output still holds.

**`pingpong`** removes the old `*.json` of `$WORK/pingpong`, generates the ping-pong set into it
(`--set pingpong` with the per-card profile and `--all-ports` the every-port one; one `NET`
and one `PATHS`, `RUNS` runs, 1000 iterations up to 128 MiB and 200 at 512 MiB) and submits
its 4 jobs: the pairs node 1 with nodes 2, 3 and 4, each with three variants (`md` with the
campaign's UCX settings, `default` with no UCX variable, `dual` with both ports of the card and
two rails), and `pw8`, the node total: a pairwise permutation of every rank of node 1 against
node 3, one port per card. Each submit adds a line to `$WORK/run_log.tsv`. All 4 jobs
are submitted at once; node 1 is in every pair and every config asks for `--exclusive`, so
Slurm runs them one after another, about 1.5 to 2 hours in total. `bringup.sh status` shows the queue and the last submits.

**`analyze pingpong`** runs `analyze.py` on the data directories `$DATA_ROOT/*/pp_*` into
`$WORK/results_pingpong/` and prints the first 60 lines of its `summary.md`.

A good result: `md` and `default` agree, `dual` is about twice `md` at large sizes (two rails),
and the `pw8` node total is about 8 times the md bandwidth of the pair n1-n3 (one port per card,
every card busy).

**`pw N...`** repeats the node total for other numbers of ranks per node, one `N` at a time (for
example `pw 4 8`). For each `N` it generates the ping-pong set with `--ranks N` into
`$WORK/pw/ppn<N>` (the old `*.json` there are removed) and submits only the node-total file
`pp_n1n3_pw<N>_*.json`, with `N` ranks per node: a pairwise permutation of the ranks of node 1
against node 3, on every `ppn/N`-th card of the per-card profile (`pw4` on 8 cards uses cards
0, 2, 4 and 6). The file holds two experiments: `pw<N>` with the campaign's UCX settings and
`pw<N>d` with no UCX variable. It is one run of many consecutive iterations rather than several
short runs: `ITER` iterations at every size (default 10000), `WARMUP` warmup iterations (default
200) and `PW_RUNS` runs (default 1); above 1000 iterations the apps also get `-maxsamples`, so
Blink keeps every sample. `NET` and `PATHS` work as for `pingpong`. Every submit adds a line to
`$WORK/run_log.tsv`; the jobs queue one after another as `--exclusive` jobs on the same nodes.

**`analyze pw`** runs `analyze.py` on the data directories that `$WORK/run_log.tsv` records for
the configs under `$WORK/pw/` (the latest submit of each config file; older `pp_*` directories are
left out) into `$WORK/results_pw/` and prints the first 60 lines of its `summary.md`. The ceilings
table has, for each `N`, `pw<N>/r` (per rank) and `pw<N>/n` (per node: per rank times `N`), and
the same with `d` for the run with no UCX variable. The lines stay within 120 columns.

`bringup.sh check` marks the `dual` experiments `CHECK`: they select the NICs through
`UCX_NET_DEVICES` instead of the per-rank shim, so there are no "local rank -> UCX_NET_DEVICES"
lines to count. That is expected.

## Diagnose binding and the bandwidth cap

```bash
examples/nplb/diagnose.sh
```

One command, no arguments, for the H100 cluster. Part A finds which `mpirun` flags pin rank i to
the i-th core of the profile's cpu list (8 ranks on one node, `--report-bindings`). Part B finds
where the per-node bandwidth cap comes from: it runs `nic_check.sh` on subsets of the NICs and on
two node pairs. Every step runs in its own `salloc`, is echoed first, and a failing step is
recorded as `FAILED` while the script goes on. The full output goes to `$WORK/diagnose_<time>.log`,
and a summary of at most 20 lines to `$WORK/diagnose_<time>_summary.txt`. It needs `profile8.json`
and `profile16.json` in `PROFILE_DIR` and at least 3 nodes in `PARTITION`. It exits 0 when every
step ran, 1 when a step failed and 2 when the setup is missing.

## Smoke test on another cluster

Before the real campaign, the whole chain can run on any Slurm cluster with RDMA NICs and at least
4 nodes (the configs need 4 nodes; 3 also works):

```bash
srun -N4 --ntasks-per-node=1 --exclusive -A <account> -p <partition> -t 00:05:00 \
     .venv/bin/python examples/nplb/probe.py --json > probe.jsonl
.venv/bin/python examples/nplb/probe.py --merge probe.jsonl --out profile.json
.venv/bin/python examples/nplb/make_configs.py profile.json --out smoke/ --smoke
for f in smoke/smoke_md_p0_C0_P1.json smoke/smoke_md_p1_C0_P1.json \
         smoke/smoke_md_p2_perm_C0_P1.json smoke/smoke_md_p2_perm_C2_P1.json \
         smoke/smoke_col_a2a_C0_P1.json; do
    .venv/bin/crab run -p <preset> "$f"
done
# when the five jobs are done:
.venv/bin/python examples/nplb/analyze.py data/<preset>/smoke_* --out smoke-results/
```

C0 and C2 mean nothing on such a cluster; running both only exercises the C0-vs-C2 tables. A good
result: every job completes, each `bindings_app_*.log` shows one card per local rank, and
`summary.md` has bandwidth, spread and slowdown values for every experiment.

## GPU comm-only suites

`gpu_set.py` writes two configs, one per victim, with Blink's communication-only programs running
on GPU memory (`-gpu`, the CUDA-aware build under `~/crab/lab/blink-gpu/build-cuda/bin`). It uses
the standard library only and does not read the cluster profile. The configs it wrote for the
H100 nodes are in [`h100/`](h100/README.md).

- `gpu_a2a_comm.json`: victim `alltoall_comm_only`.
- `gpu_ag_comm.json`: victim `allgather_comm_only`.

Each file has 8 experiments: `ppn{1,2,4,8}_alone` (the victim alone, the reference) and
`ppn{1,2,4,8}_vs_a2a` (the same victim while an aggressor runs). A victim is a chain of 11 apps,
one per message size, 1 KiB to 256 MiB in steps of ×4 and then 512 MiB, each starting when the
previous one ends (1000 iterations and 100 warm-up iterations below 32 MiB, 100 and 50 from there
up). The aggressor is `alltoall_comm_only -endl` without `-blength`, so it is steady; it is not
collected, starts 3 seconds before the victim and stops when the last size ends. It always runs
the maximum `ppn` of the file (8, or 2 with `--local`), whatever the victim's `ppn`, with 128 KiB
messages. The 4 nodes are split in two interleaved partitions of 50%: the victim runs on Slurm
nodes 0 and 2, the aggressor on nodes 1 and 3.

```bash
.venv/bin/python examples/nplb/gpu_set.py --out examples/nplb/h100/            # the two lab files
.venv/bin/python examples/nplb/gpu_set.py --out smoke/ --smoke                 # a short version of both
.venv/bin/python examples/nplb/gpu_set.py --out local/ --local                 # laptop files, no GPUs
.venv/bin/python examples/nplb/gpu_set.py --out examples/nplb/h100/ --aggr-ppn 4 --aggr-msgsize 1048576
.venv/bin/python examples/nplb/gpu_set.py --out local/pair --nodes <nodeA>,<nodeB> --tag same_leaf   # 2 nodes, victim alone
```

- `--out DIR` (required): the folder for the two files; it is created when missing.
- `--smoke`: files named `smoke_*`, with only `ppn` 1 and 8, two sizes (1 MiB and 64 MiB),
  `-iter 20 -warmup 5` and a 30 minute walltime. Run it first to check the launch before the
  6 hour full files.
- `--local`: files named `local_*`, only to drive the flow (generate, check, launch, parse) on a
  laptop without GPUs or InfiniBand: `ppn` 1 and 2, sizes 1 KiB and 1 MiB, `-iter 5 -warmup 1`, no
  `-gpu`, `binary`, `nics`, `cpus` or UCX variables. The layout is 2 nodes (`numnodes` "2", `ppn`
  "2"), one per partition, so it needs a preset with two hosts (see
  [Running the laptop files](#running-the-laptop-files)). It cannot be combined with `--smoke`.
- `--aggr-ppn`: the aggressor's ranks per node: 1, 2, 4, 8 or `same` (the victim's `ppn`). The
  default is the maximum `ppn` of the file: 8 (2 with `--local`). A comma list is allowed.
- `--aggr-msgsize`: the aggressor's message size in bytes (default 128 KiB = 131072). A comma list
  is allowed.
- `--nodes A,B` with `--tag NAME`: 2-node files with the victim alone only, for when the 4th node
  is offline (see [Two nodes, victim alone](#two-nodes-victim-alone)). `--out` must be inside a
  `local/` directory. It cannot be combined with `--local`, `--aggr-ppn` or `--aggr-msgsize`.

With more than one (aggressor `ppn`, size) pair, every victim `ppn` gets one `vs` experiment per
pair, named `ppn{p}_vs_a2a_ap{aggressor ppn}_as{size}` instead of `ppn{p}_vs_a2a`.

**Pinning.** The ranks of a node use the first `ppn` entries of the `ppn` 8 layout: local rank
*i* runs on core 0, 1, 2, 3, 40, 41, 42, 43 (the *i*-th of these), on port `mlx5_0:1`,
`mlx5_2:1`, `mlx5_4:1`, `mlx5_8:1`, `mlx5_10:1`, `mlx5_12:1`, `mlx5_14:1`, `mlx5_16:1` (the *i*-th
of these), and on GPU *i* (Blink's `-gpu` picks the GPU by local rank, and
`CUDA_DEVICE_ORDER=PCI_BUS_ID` fixes the device order). The apps also set
`UCX_MAX_RNDV_RAILS=1` and `UCX_IB_ROCE_REACHABILITY_MODE=all`. The cores and ports are those of
the H100 test cluster; on other hardware change `CORES` and `NICS` in `gpu_set.py`.

**Preset.** The configs launch through the CUDA-aware Open MPI, so run them with the preset
`nplb_h100_gpu` (see [`presets/README.md`](presets/README.md)), not `nplb_h100`:

```bash
.venv/bin/crab run -p nplb_h100_gpu examples/nplb/h100/smoke_gpu_a2a_comm.json   # smoke first
.venv/bin/crab run -p nplb_h100_gpu examples/nplb/h100/gpu_a2a_comm.json         # then the full file
```

**Aggressor.** The aggressor runs `ppn` 8 (the maximum) in every experiment and sends 128 KiB
messages, as decided by the supervisor, to be confirmed against the ISC paper of the previous
campaign. To try another choice, regenerate with `--aggr-ppn` and `--aggr-msgsize` (`--aggr-ppn
same` follows the victim's `ppn`).

### Two nodes, victim alone

When the 4th node is offline the 4-node files cannot run. `--nodes` writes the victim-alone part
on two nodes instead, so the two victims can still be measured while the node is away:

```bash
# same leaf switch: the baseline pair
.venv/bin/python examples/nplb/gpu_set.py --out local/pair --nodes <nodeA>,<nodeB> --tag same_leaf
# nodes behind different leaf switches: the comparison
.venv/bin/python examples/nplb/gpu_set.py --out local/pair --nodes <nodeA>,<nodeC> --tag cross_leaf
# the same, a short version first (ppn 1 and 8, two sizes)
.venv/bin/python examples/nplb/gpu_set.py --out local/pair --nodes <nodeA>,<nodeB> --tag same_leaf --smoke
.venv/bin/crab run -p nplb_h100_gpu local/pair/gpu_a2a_comm_same_leaf.json
```

- `--nodes A,B`: the two nodes, written as `--nodelist=A,B` in the config's `sbatch_directives`,
  with `numnodes` "2" and a plain `linear` allocation. Use the Slurm node names; pick a pair on
  the same leaf switch for the first run and a pair on different leaves for the comparison.
- `--tag NAME`: goes in the file names (`gpu_a2a_comm_NAME.json`, `gpu_ag_comm_NAME.json`, with
  `smoke_` in front for `--smoke`), so one `--out` folder can hold both pairs. Letters, digits,
  `-` and `_` only.
- `--out local/...`: required, and the generator refuses any other folder. The files hold node
  names, which are never committed, and `local/` is git-ignored.
- `--smoke`: the same short run as above (`ppn` 1 and 8, sizes 1 MiB and 64 MiB, `-iter 20
  -warmup 5`).

Each file has the experiments `ppn1_alone`, `ppn2_alone`, `ppn4_alone` and `ppn8_alone` (`ppn1_alone`
and `ppn8_alone` with `--smoke`): the same victim chain, pinning, binary and environment as the
`alone` experiments of the 4-node files, with no aggressor and no partitions. Run them like the
others, with `crab run -p nplb_h100_gpu <file>`.

### Check the H100 nodes before a GPU run

`gpu_preflight.sh` runs on the node that holds the checkout and only reads: it changes nothing on
any node. The only file it writes is the counter snapshot, on the machine it runs on.

```bash
examples/nplb/gpu_preflight.sh <node1> <node2> <node3> <node4>        # or: PARTITION=<partition> examples/nplb/gpu_preflight.sh
examples/nplb/gpu_preflight.sh counters snapshot before.txt <node1> <node2> <node3> <node4>
examples/nplb/gpu_preflight.sh counters diff before.txt <node1> <node2> <node3> <node4>
```

- First line: the checks. Run it before every session, and again when a node was rebooted. It
  prints one `OK`, `WARN` or `FAIL` line per node and check, and exits 1 on any `FAIL`. A `FAIL` line
  names the node and what is missing, for example `not ACTIVE: mlx5_3 (1: DOWN)`. If a node or a
  port is down, stop and tell the lab: nothing here fixes it. The checks are `state` (Slurm does not
  list the node as down, drained, failed or in maintenance), `ports` (the 16 CX8 ports `mlx5_0` to
  `mlx5_5` and `mlx5_8` to `mlx5_17` exist and port 1 is `ACTIVE`; the disabled CX5 `mlx5_6` and
  `mlx5_7` are skipped), `gpus` (`nvidia-smi -L` lists 8), `peermem` (`nvidia_peermem` is loaded,
  which GPUDirect RDMA needs), `mpirun` (`$LAB/ompi-cuda/bin/mpirun` runs and `ompi_info` reports CUDA
  support), `ucx` (`$LAB/ucx-cuda/lib` has `libucp`), `blink` (both comm-only programs exist and `ldd`
  finds every library, with the lab's `LD_LIBRARY_PATH`), `nfs` (on the workers: the checkout is an NFS
  mount) and `sr` (the output of `sr_state.sh`: a `WARN` when Selective Repeat differs between ports,
  a `FAIL` when a port could not be read).
- Second line: saves five counters of each of the 16 ports of every node into `before.txt`, one
  `<node> <port> <counter> <value>` line each: `np_ecn_marked_roce_packets`, `np_cnp_sent` and
  `rp_cnp_handled` (ECN marks seen and congestion notifications sent and handled) and
  `port_xmit_data` and `port_rcv_data` (traffic, in units of 4 bytes). A counter this driver does not
  have is stored as `NA`. Run it right before the smoke run.
- Third line: reads the counters again right after the run and prints the nonzero change of each
  (`<node> <port> <counter> +<delta>`), then three summary lines. `ports with traffic` names the
  ports whose traffic counters moved (`<node>/<port>`): with one rank per port, every port of the
  nodes that ran ranks should be listed, and a node or port that carried nothing is the thing to look
  at. `ECN/CNP activity` is `yes` when any ECN or CNP counter moved, `no` when none did and `unknown`
  when this driver exposes none of them. The last line, `proxy: ...`, is the caveat to keep in mind:
  these counters show whether the NICs saw ECN marks and CNPs during the run. They are a proxy
  for congestion-control activity, not the NPLB setting of the switch, which nodes cannot read. Without
  hosts, `diff` uses the hosts in `before.txt`. It exits 1 when a node is unreachable and 2 when the file
  is missing or malformed.

`LAB` (default `/root/crab/lab`), `CRAB_DIR`, `CUDA_LIB` (default
`/usr/local/cuda-12.3/targets/x86_64-linux/lib`, or the newest `cuda-12.*` when 12.3 is gone) and `PREFLIGHT_SSH` change where it looks and how
it reaches the nodes; `examples/nplb/gpu_preflight.sh -h` lists them all.

### One-screen lab session

`gpu_lab.sh` runs the lab checks as steps. Every step prints at most one screen, 38 lines of at
most 100 columns, so it can be photographed and sent for diagnosis, and writes everything it saw to
a log. So far it has three steps, `status`, `smoke` and `dual-port`; the other steps (`pin`, `all`)
print `gpu_lab: step '<name>' is not available yet` and exit 2. `status` changes nothing on any
node. `smoke` starts one real job, the smoke config, through the same plain `crab run` the final
suites use; it changes no node setting and never cancels the job. `dual-port` starts only benchmark
processes (Blink through `mpirun`, `ib_write_bw`) on two nodes it allocates for itself, and changes
no node setting; the step lives in `gpu_lab_dual.sh`, which `gpu_lab.sh` loads.

```bash
# What: check the setup (preset, the three configs, every node). Why: a missing preset or a node that is
# down would waste the smoke job. When: first, on the head node in the checkout. Nodes default to those
# of PARTITION; name them to check a subset.
examples/nplb/gpu_lab.sh status                    # or: examples/nplb/gpu_lab.sh status <node1> <node2> <node3> <node4>

# What: submit the smoke config, wait, summarize. Why: it proves that a real job runs end to end and measures
# what the full suites cost. When: after `status` is green. The nodes are only the ones whose NIC
# counters are read; they default to those of PARTITION.
examples/nplb/gpu_lab.sh smoke                     # or: examples/nplb/gpu_lab.sh smoke <node1> <node2> <node3> <node4>

# What: measure one H100 card with one port and with both ports, from GPU memory, through UCX and raw. Why: to
# learn how much the second port adds and whether both ports share one PCIe x16. When: after `smoke`, or
# any time the GPU numbers look capped. The card is 0-7 (default 0); NODES picks the two nodes, else the
# step asks salloc for two exclusive nodes of PARTITION and waits up to ALLOC_TIMEOUT seconds (600).
# Same-leaf pairs give the cleanest numbers: name two nodes behind one leaf switch (see `topology.sh`).
examples/nplb/gpu_lab.sh dual-port                 # or: examples/nplb/gpu_lab.sh dual-port 3
NODES="<node1> <node2>" examples/nplb/gpu_lab.sh dual-port 5

# What: the same step inside an allocation you already hold. Why: salloc is then not asked for a second
# one. When: you ran `salloc -N 2 ...` yourself; the two nodes come from the allocation.
GPU_LAB_ALLOC=0 examples/nplb/gpu_lab.sh dual-port

# What: print the whole log of the last run of a step. Why: the screen cuts lines to 100 columns and to
# 38 lines. When: a line ends in `~` or says `... N more lines`.
examples/nplb/gpu_lab.sh show status
examples/nplb/gpu_lab.sh show smoke
examples/nplb/gpu_lab.sh show dual-port

# What: the same smoke with the all-gather config. Why: a one-variable switch between the two victims.
# When: after the all-to-all smoke, if the all-gather suite is also going to run.
SMOKE_JSON=examples/nplb/h100/smoke_gpu_ag_comm.json examples/nplb/gpu_lab.sh smoke
```

- `status`: the first thing to run on the head node, in the checkout, before anything else, and
  again after a node was rebooted. Photograph the screen. Without nodes it uses those of
  `PARTITION` (default `h100_4nodes`, from `sinfo`). The screen has one line per
  check, `<what> OK|WARN|FAIL <detail>`: `checkout` (branch and commit, with a note when files are
  modified), `preset` (a `*.json` file that defines `nplb_h100_gpu` is in the overrides folder; when
  it is not, the next line is the `cp` command that installs it), and `validate smoke`,
  `validate a2a` and `validate allgather` (`crab validate -p nplb_h100_gpu` on the three GPU configs
  in `h100/`; it reads the file and the preset only, it does not check that `binary` exists). Below
  them is the grid: one row per node, one column per `gpu_preflight.sh` check (`ok`, `WARN`,
  `FAIL`, or `-` where the check does not apply, such as `nfs` on the head node; an `ssh` column
  appears when a node cannot be reached). Under the grid, each cell that is not `ok` gets one line,
  `<node> <check> <detail>`. The last line is the verdict, `status: N FAIL, M WARN`. The exit status
  is 0 when `N` is 0 and 1 otherwise; `status` always runs every check, whatever fails.
- `smoke`: run it once `status` is green. It is the rehearsal of the final submit: the same
  `crab run -p nplb_h100_gpu --json <smoke config>` from the checkout, then a wait on
  `crab status --json <job id>` every 10 s (a single updating `waiting:` line shows only on a
  terminal; the limit is 30 minutes) and a read of the run folder `crab run` printed. Before it
  submits anything it checks, in this order, that crab and the smoke config exist, that the preset is
  installed and that `crab validate -p nplb_h100_gpu` accepts the config; a failure there is a
  `FAIL` line and nothing is submitted. The full node check is not run here, that is `status`. The
  screen then shows:
  - `job`: the job id, its final state (`COMPLETED` is the only good one), the run time from the
    worker log and the total time since the submit; and `apps`: `8/8 victim apps finished` and how
    many CSV rows were written (the aggressor app, which has no CSV and is stopped at the end of its
    experiment, is not counted);
  - one row per experiment: `apps` done/expected, the median duration per message size (the median
    over the CSV rows of the `*_Median-Duration_s` column, shown as `us`, `ms` or `s`), the rows of
    each victim CSV (`-` where the file is missing) and the wall time of each victim app, taken from
    the worker log (the `FINISHED` or `FAILED` line minus the `Launched` line; `?` when the lines
    are missing, which is not an error);
  - `NIC counters, before and after the job`: the three summary lines of
    `gpu_preflight.sh counters diff` (the ports that carried traffic, whether an ECN/CNP counter moved,
    and the reminder that these counters are a proxy for congestion control, not the NPLB setting of
    the switch). The counters are saved before the submit and read again when the job ended, on the
    nodes you gave or those of `PARTITION`; a node that cannot be read gives a `counters WARN` line
    and the smoke result still counts;
  - `full suites: ~A + ~B min`: a rough, measured estimate of the two full suites (all-to-all and
    all-gather). For each app it adds a startup time (the shortest app wall time seen in this run) to
    `-iter + -warmup` times the time of one iteration, which is the median of the median-duration
    column at the two smoke sizes, extended to the 11 sizes of `h100/gpu_a2a_comm.json` in a straight
    line on log-log axes and never below the smaller measured time; ppn 2 and 4, which smoke does not
    run, borrow the nearest ppn it ran. All-gather uses the same time model and the same numbers as
    all-to-all. The line also says how many iterations smoke ran per app against the 150 to 1100 of
    the full suites, because that is what makes it rough;
  - the verdict, `smoke: N FAIL, M WARN`.

  A job that ends `FAILED`, `CANCELLED`, `TIMEOUT` or `NODE_FAIL`, a `crab run` that fails, a victim
  app with no CSV, no rows or no `FINISHED` line, are each a `FAIL` with a one-line reason and
  `details: gpu_lab.sh show smoke`; the first line of an `error_app_<id>.log` or `slurm_error.log`
  follows, cut to the screen width (the log has it whole). If the job is still not over after the
  limit, `smoke` stops waiting, prints `crab status <job id>` and `crab cancel <job id>` and leaves
  the job alone: cancelling it is your decision. The exit status is 0 when the job completed and
  every victim app has rows, 1 otherwise, and 2 for bad usage.
- `dual-port [CARD]`: the question is "push both ports of one H100 card from GPU memory: how much do
  we get, and does the second port go over the same PCIe lanes?". It is a measurement, not a gate: a
  `FAIL` means a run gave no number, a `WARN` marks a suspicious value, everything else is a plain
  fact. A CX8 card has two PCI functions on one bus, the even `mlx5_N` and the odd `mlx5_N+1`; the
  cards, in order 0 to 7, are `mlx5_0`+`mlx5_1`, `mlx5_2`+`mlx5_3`, `mlx5_4`+`mlx5_5`,
  `mlx5_8`+`mlx5_9`, `mlx5_10`+`mlx5_11`, `mlx5_12`+`mlx5_13`, `mlx5_14`+`mlx5_15`,
  `mlx5_16`+`mlx5_17` (`mlx5_6` and `mlx5_7` are the unused CX5). The GPU of card k is GPU k behind
  the same PCIe switch, and the rank or process runs on core 0, 1, 2, 3 (cards 0 to 3, socket 0) or
  40, 41, 42, 43 (cards 4 to 7, socket 1). The step needs 2 nodes; outside a Slurm allocation it runs
  itself again under `salloc -N 2 -p <PARTITION> --exclusive --immediate=<ALLOC_TIMEOUT>` (plus
  `--nodelist=` when `NODES` is set) and prints one `allocation FAIL` line, exit 1, when none was
  granted in time; it never cancels or touches a job of anyone else. Inside an allocation
  (`SLURM_JOB_ID` set, or `GPU_LAB_ALLOC=0`) it takes the two nodes from `scontrol show hostnames`.
  Four measured runs follow, each between a counter snapshot and a counter diff on both nodes:
  - `ucx 1 port` and `ucx 2 ports`: Blink `pingpong_b -gpu` through the CUDA `mpirun` of the lab
    (`--mca pml ucx`, one rank per node, bound to the card's core), with `UCX_NET_DEVICES` set to the
    first port (`UCX_MAX_RNDV_RAILS=1`) and then to both ports (`UCX_MAX_RNDV_RAILS=2`), and
    `UCX_IB_ROCE_REACHABILITY_MODE=all`, `CUDA_DEVICE_ORDER=PCI_BUS_ID` and `CUDA_VISIBLE_DEVICES=<card>`
    on both. `UCX_LOG_LEVEL` is never set, it changes the timing. Gb/s is the message size times
    8 over 1e9 times the one-way time of one sample, which is half a round trip (the `pingpong_b`
    source records the round trip divided by 2), taken as the median over the samples of the slowest
    of the two ranks (the Maximum column of Blink's CSV);
  - `raw 1 port` and `raw 2 ports`: `ib_write_bw --use_cuda=<card> -R --report_gbits` from node A
    (client) to node B (server), started over ssh, first on the first port and then on both ports at
    the same time, each process on its own core (the card's core, and the core 4 above it for the
    second port, because two busy-polling processes must not share one core). Gb/s is the `BW
    average` column, summed over the ports. The addresses of the ports on node B are read from their
    network interfaces. Without `--use_cuda` in `ib_write_bw` (perftest built without CUDA) the raw
    part is skipped with a `WARN`, and the UCX part still runs.

  The screen shows the nodes and the card, the four runs with their Gb/s, the ratio of two ports to
  one for UCX and for raw, the reference (both ports of a card reached 418-435 Gb/s on the host, a
  PCIe Gen5 x16 cap; one port is 400 Gb/s) and the unit (Gb/s = 1e9 bit/s; the Gibit/s of the earlier
  lab runs are 7.4% lower), then for each run whether each of the two ports carried traffic (and
  whether an ECN/CNP counter moved), and a verdict line for UCX and one for raw: ratio 1.6 or more
  says `second port adds bandwidth`, from 1.1 to 1.6 `partial gain`, below 1.1 `no gain from the
  second port`; when the figure with two ports is at or below 456 Gb/s (5% above the 435 Gb/s
  ceiling) and the ratio is below 1.6, `consistent with a shared PCIe x16 cap` is added. A `WARN` is
  also printed for a one-port figure above 400 Gb/s, a two-port figure above 800 Gb/s, two ports
  slower than one, no traffic on the first port, no traffic on the second port in a two-port run,
  and traffic on the second port in a one-port run. A good result: both ratios near 2 with both
  ports shown carrying traffic, or a clear plateau near 420-435 Gb/s that says the cap is the PCIe
  link and not the network. Settings: `SIZE` (message bytes, default 134217728), `DUR` (seconds of
  each `ib_write_bw`, default 10), `DUAL_ITER` and `DUAL_WARMUP` (Blink `-iter` and `-warmup`, default
  50 and 10), `STEP_TIMEOUT` (seconds each launched command may run, default 180, bounded with
  `timeout -k 5`; the `ib_write_bw` processes also end by themselves after `DUR`), `RAW_SETTLE`
  (seconds between starting the servers and the clients, default 3), `LAB` (default
  `/root/crab/lab`), `BLINK` (default `$LAB/blink-gpu/build-cuda/bin`), `CUDA_LIB`, `SALLOC` (the
  `salloc` executable), `ALLOC_TIMEOUT`, `GPU_LAB_ALLOC` and `NODES`. The exit status is 0 when no
  run failed, 1 when one did, and 2 for a bad card or setting (one line, nothing started).
- `show <step>`: prints the full log of the last run of that step, which is what the screen cut.
  Use it when a line ends in `~` (cut at 100 columns) or when a line says `... N more lines`.

A good result: `status: 0 FAIL, 0 WARN` and `smoke: 0 FAIL, 0 WARN`; `dual-port` is read, not passed. A `FAIL` names its cause: fix it
(the preset is copied with the `cp` line on the screen; a node or a port that is down is for the lab
to fix) and run the step again before going on.

Where things go. Each run makes a folder `$WORK/gpu_lab/<YYYYmmdd_HHMMSS>/` with one
`<step>.log` per step (the whole output of `gpu_preflight.sh` and of `crab validate`, for `smoke`
the submit result, the state changes of the job with their times, the submit, start and end times,
how the wall times were derived, every CSV, the estimate model and the verdict), and
`$WORK/gpu_lab/latest` points at the newest. `WORK` defaults to `local/nplb-work` in the checkout, as
for `topology.sh`. Other settings: `CRAB` (the crab executable, default `.venv/bin/crab`),
`CRAB_PRESETS_DIR` (the folder searched for the preset; by default the one `crab info` prints, then
`~/.config/crab/presets/`), `SMOKE_JSON` (the config `smoke` submits; the all-gather smoke file
`h100/smoke_gpu_ag_comm.json` is the one-variable switch), `SMOKE_POLL` and `SMOKE_TIMEOUT` (seconds
between two status calls, and the limit of the wait), `PYTHON` (reads the results; default
`.venv/bin/python`, else `python3`), `PREFLIGHT_SSH` and `GPU_PREFLIGHT`;
`examples/nplb/gpu_lab.sh -h` lists them all.

### Running the laptop files

The `local_*` files ask for 2 nodes, and the `local` preset has one host, so CRAB refuses them
(`numnodes 2 is more than the preset's 1 host(s)`). Give CRAB a preset whose two hosts are
both this machine:

```json
{
    "local2": {
        "scheduler": "local",
        "launcher": "mpirun",
        "hosts": ["localhost:6", "127.0.0.1:6"]
    }
}
```

- `"local2"`: the preset name, used as `-p local2`.
- `"scheduler": "local"`: run on this machine as a detached process, with no Slurm.
- `"launcher": "mpirun"`: start each app with Open MPI's `mpirun`, which places the victim ranks on
  one host and the aggressor ranks on the other.
- `"hosts"`: two distinct names (host names must be distinct) that both reach this machine, each
  with 6 cores. The victim partition gets the first host and the aggressor partition the second.

Save it as a file (for example `local2.json`) in `local/presets/` in a checkout, or in the folder
that `CRAB_PATH_PRESETS` names (it must be a folder, not a file). Then check it and run a file:

```bash
.venv/bin/crab presets show local2                         # the preset as CRAB reads it
.venv/bin/crab run -p local2 local/local_gpu_a2a_comm.json  # the generated file, from the --local command above
```

The local files do not write `binary`, so the apps need a Blink receipt (`crab setup`) or a
`binary` you add to each app. The run's worker is a detached process: start it from a normal shell
session, not from a command that kills its children when it ends.

## The two test sets

The **md set** follows the campaign plan; the **colleague set** keeps the earlier Leonardo
congestion configs and adapts them to this hardware. Where the two differ, both are run.

| | md set (`md_*`) | colleague set (`col_*`) |
|---|---|---|
| Victims | `pairwise_nb -mode rot`, `alltoall_nb`, `allgather_nb` | `alltoall_comm_only`, `allgather_comm_only`, `allreduce_nb` |
| Sizes | 1 B to 128 MiB in steps of ×8, plus 512 MiB | 8 B to 16 MiB in steps of ×8 |
| Iterations | 1000 up to 16 MiB, 100 at 128 MiB, 25 at 512 MiB | 1000 |
| Aggressors | `alltoall_nb` steady, `alltoall_nb` bursts, `incast_nb` (2 MiB) | `alltoall_man` bursts (2 MiB) |
| Bursts | on {0.001, 0.01, 0.1} s × off {1e-6, 1e-4, 1e-2} s | same grid |
| Baseline | `<victim>_alone`, same partitions, no aggressor | none |
| Runs | 10 to 20 (20 to 40 for C0) | 1 |
| Aggressor start | 3 s before the victim | with the victim |
| Networks | C0, C2 (+ C3 phase 1) | C0, C2 |

Message sizes of one test run one after the other in the same experiment (a chain), so the
aggressor runs once for the whole sweep and stops when the last size ends.

### md set phases

- **Phase 0, ceilings** (`md_p0_*`, 2 ranks, one per node): `p0_cross` pingpong between Server1
  and Server3 (crosses the spines); `p0_local` pingpong between Server1 and Server2, the host and
  NIC ceiling. `p0_local` assumes the same NIC index on Server1 and Server2 reaches the same leaf:
  check it against the wiring.
- **Phase 1, isolated** (`md_p1_*`, all ranks): `p1_perm` (every rank sends to the matching rank
  in the other pod), `p1_a2a`, `p1_ag`. With 3 nodes, `p1_perm` runs on Server1+Server3 only.
- **Phase 2, victim vs aggressor** (`md_p2_<victim>_*`): the victim on the victim partition alone,
  then against each aggressor on the aggressor partition. With 3 nodes the aggressor is a
  pairwise rotation on Server2 (`-offset ppn/2`, so each card on one leaf sends to a card on the
  other) with `UCX_TLS=rc,self`, so its traffic goes through the NICs instead of shared memory.

## Choices to confirm

The generator makes these choices where the campaign plan leaves them open. Change them with the
flags, or in the scripts, if the test owners decide otherwise:

- The aggressor message size is 2 MiB and the burst grid is the one of the earlier Leonardo
  configs; the incast aggressor also uses 2 MiB.
- Iterations at 128 MiB and 512 MiB (100 and 25).
- `maxruns` is twice `minruns` in the md set.
- A size is skipped when its buffers need more than half the node's memory.
- `p0_local` uses Server1 ↔ Server2 with the same NIC index.
- The 3-node aggressor offset is half the ranks per node.

## Run log

Keep one line per job, next to the results:

| Date and time | Network (C0/C2/C3) | Paths | Config file | Job id | Data dir | Notes |
|---|---|---|---|---|---|---|
| | | | | | | |

Also record once: node list and order, NIC link speeds and ports (from the probe), NIC-to-leaf
wiring, message sizes skipped (from the generator summary), and any anomaly.
