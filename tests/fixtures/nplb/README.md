# NPLB follow fixtures

Excerpts of two real runs of the NPLB campaign on the H100 lab cluster, taken from the result archives
of 2026-10-29 (the Slurm job logs the worker wrote with ANSI colour codes, and the data root's
`metadata.csv`). They test how `examples/nplb/follow.py` decides how a job ended on a cluster with no
Slurm accounting, so the line shapes are the engine's own.

| Run | What it was | What the log and `metadata.csv` say |
|-----|-------------|--------------------------------------|
| `a2a_run1` | all-to-all victim, 4 experiments x 11 apps (`gpu_a2a_comm_same_leaf_2026-10-29_16-04-13-621184`) | the log ends `Engine run finished  elapsed=0:10:48`, but `ppn4_alone` and `ppn8_alone` are `FAILED` in `metadata.csv`: 3 apps failed with `exit=17` |
| `ag_run1` | all-gather victim, 4 experiments x 11 apps (`gpu_ag_comm_same_leaf_2026-10-29_16-35-53-182977`) | the log ends `Engine run finished  elapsed=0:12:21`, every experiment `COMPLETED` |

Files, per run (`<run>` is `a2a_run1` or `ag_run1`):

- `<run>_log_head.txt`: the first 25 lines of `slurm_output.log`, raw (ANSI codes kept).
- `<run>_log_tail.txt`: the last 15 lines of `slurm_output.log`, raw. For `a2a_run1` they include
  the benchmark's own output of a failed app (MPI and UCX error lines without a timestamp).
- `<run>_crab_lines.txt`: every line of `slurm_output.log` written by the engine itself (source
  `CRAB`), raw; the benchmark's dumped output (source `APP` and the raw lines below it) is left out.
  Used as the log of a finished run.
- `<run>_metadata.csv`: the data root's `metadata.csv` as it was when the run ended (it also lists
  the earlier smoke run).
- `a2a_run1_error_app_10.log`: the first 8 lines of `ppn4_alone/error_app_10.log`, the UCX
  `ibv_reg_mr ... Bad address` failure.

Anonymised on 2026-11-10: the node names are `nodeA` and `nodeB`, the home path is `/home/user`.
The files held no user names, addresses or credentials. Nothing else was edited, so the timestamps,
PIDs and byte counts are the real ones.
