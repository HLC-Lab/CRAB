"""Slurm: the `#SBATCH` header, the job script, `sbatch`, job status and cancel, and node lists."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from typing import Any

from crab.core.execution.scheduler.base import CancelResult, JobStatus, worker_command
from crab.log import CrabLogger

# A command runner returns stdout as text. It raises FileNotFoundError when the
# binary is absent and subprocess.CalledProcessError on a non-zero exit — both
# are caught by the scheduler methods for graceful degradation. Injectable for tests.
CommandRunner = Callable[[list[str]], str]


def _default_runner(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)


def _split_nodelist(s: str) -> list[str]:
    """Split a sinfo nodelist on top-level commas only (not inside brackets)."""
    result, depth, current = [], 0, []
    for ch in s:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            if current:
                result.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        result.append("".join(current))
    return result


def _expand_nodelist_token(token: str) -> list[str]:
    """Expand 'prefix[r1,r2]' into ['prefix[r1]', 'prefix[r2]']; pass plain tokens through."""
    start, end = token.find("["), token.rfind("]")
    if start == -1 or end == -1 or end < start:
        return [token]
    prefix, inner = token[:start], token[start + 1 : end]
    return [f"{prefix}[{r}]" for r in inner.split(",") if r]


class SlurmScheduler:
    """Submits CRAB jobs to Slurm.

    Args:
        logger: receives the submit messages and the header warnings.
        crab_root: the checkout root; its `.venv/bin/activate`, when present, is sourced by the
            job script.
        runner: runs the status, cancel and node-description commands (`squeue`, `sacct`,
            `scancel`, `sinfo`) and returns stdout; defaults to a real subprocess call.
    """

    def __init__(
        self, logger: CrabLogger, crab_root: str, runner: CommandRunner | None = None
    ) -> None:
        self.log = logger
        self.crab_root = crab_root
        self._run = runner or _default_runner

    def generate_header(self, global_opts: dict[str, Any], data_directory: str) -> list[str]:
        """Generates the list of #SBATCH lines handling defaults, overrides, and security."""
        # 1. Protected parameters (the framework always wins).
        # Map: key -> value computed by the framework.
        _numnodes = global_opts.get("numnodes")
        protected_defaults = {
            "nodes": f"--nodes={_numnodes}" if _numnodes is not None else None,
            "ntasks-per-node": f"--ntasks-per-node={global_opts.get('ppn', 1)}",
            # Common aliases to block
            "N": None,
            "n": None,  # Block -n too, in case the user passes it
        }

        # 2. Overridable defaults.
        # Map: unique key -> full directive string.
        raw_info = str(global_opts.get("extrainfo", "job"))
        safe_info = "".join([c if c.isalnum() else "_" for c in raw_info])[:10]

        directives_map = {
            "job-name": f"--job-name=crab_{safe_info}",
            "output": f"--output={os.path.join(data_directory, 'slurm_output.log')}",
            "error": f"--error={os.path.join(data_directory, 'slurm_error.log')}",
            "time": f"--time={global_opts.get('walltime', '00:10:00')}",
        }

        # Add the protected values to the map (as the base)
        for k, v in protected_defaults.items():
            if v:
                directives_map[k] = v

        # System defaults passed by the orchestrator
        system_defaults = global_opts.get("system_sbatch", [])

        # Parse the system defaults first (lower priority than the user, higher than the base)
        for raw in system_defaults:
            directive = str(raw).strip()
            if "\n" in directive or "\r" in directive:
                self.log.warning(
                    f"Skipping system_sbatch directive containing newlines: {directive!r}"
                )
                continue
            key = directive.lstrip("-").split("=")[0]
            # Never override the protected keys
            if key not in protected_defaults:
                directives_map[key] = directive

        # 3. User directives (from the JSON, final override)
        user_directives = global_opts.get("sbatch_directives", [])

        # Legacy support: a dict instead of a list is converted
        if isinstance(user_directives, dict):
            converted = []
            for k, v in user_directives.items():
                if v is True:
                    converted.append(f"--{k}")
                elif v is False:
                    continue
                else:
                    converted.append(f"--{k}={v}")
            user_directives = converted

        for raw_directive in user_directives:
            directive = str(raw_directive).strip()

            # A. Security check (newline injection)
            if "\n" in directive or "\r" in directive:
                self.log.warning(f"Skipping directive containing newlines: {directive}")
                continue

            # B. Key extraction
            # Example: "--account=ABC" -> "account"
            # Example: "--exclusive" -> "exclusive"
            # Example: "-J jobname" -> "J"
            clean_str = directive.lstrip("-")
            if "=" in clean_str:
                key = clean_str.split("=")[0]
            else:
                key = clean_str.split()[
                    0
                ]  # Handles rare cases like "-J name" passed as a single string

            # C. Conflict resolution
            if key in protected_defaults:
                self.log.warning(
                    f"User directive '{directive}' ignored. '{key}' is managed by CRAB."
                )
                continue

            if key in ["output", "error", "o", "e"]:
                self.log.warning(
                    f"User overrode log path with '{directive}'. Standard logging might be lost."
                )

            # D. Apply (last write wins for user defaults, except protected)
            directives_map[key] = directive

        # 4. Rendering: the complete directive strings
        return [f"#SBATCH {v}" for v in directives_map.values()]

    def submit(self, job_dir: str, global_opts: dict[str, Any]) -> str | None:
        """Write `crab_job.sh` into `job_dir`, submit it with `sbatch` and return the job id.

        An interrupt after sbatch accepted the job cancels it with `scancel`.

        Raises:
            RuntimeError: if sbatch refuses the job; the message carries Slurm's reason.
        """
        sbatch_headers = self.generate_header(global_opts, job_dir)

        script_path = os.path.join(job_dir, "crab_job.sh")
        cmd = worker_command(job_dir)

        with open(script_path, "w") as f:
            f.write("#!/bin/bash\n\n")

            # Write the computed directives
            for line in sbatch_headers:
                f.write(f"{line}\n")

            venv = os.path.join(self.crab_root, ".venv", "bin", "activate")
            if os.path.exists(venv):
                f.write(f"\nsource {venv}\n")

            # The setup lines passed by the orchestrator in the config
            system_header = global_opts.get("system_header", [])
            if system_header:
                f.write("\n# --- System Setup (Modules & Environment) ---\n")
                for line in system_header:
                    if "\n" in str(line) or "\r" in str(line):
                        self.log.warning(
                            f"Skipping system_header line containing newlines: {line!r}"
                        )
                        continue
                    f.write(f"{line}\n")

            f.write(f"\n{cmd}\n")

        self.log.info(f"Submitting: sbatch {script_path}")
        job_id = None
        try:
            out = subprocess.check_output(
                ["sbatch", script_path], text=True, stderr=subprocess.STDOUT
            )
            m = re.search(r"Submitted batch job (\d+)", out)
            job_id = m.group(1) if m else None
            self.log.info(out.strip())
        except subprocess.CalledProcessError as exc:
            # The output holds Slurm's reason (e.g. an invalid account); without it the user
            # only sees "exit status 1".
            reason = (exc.output or "").strip() or "no message"
            raise RuntimeError(f"sbatch refused the job (exit {exc.returncode}): {reason}") from exc
        except (KeyboardInterrupt, SystemExit):
            if job_id:
                self.log.warning(f"Interrupted — cancelling Slurm job {job_id}")
                subprocess.run(["scancel", job_id], check=False)
            raise
        return job_id

    def node_list(self) -> list[str]:
        """The hostnames of the current Slurm allocation, expanded with `scontrol`.

        Raises:
            RuntimeError: if SLURM_NODELIST is unset, or scontrol prints no hostname.
            subprocess.CalledProcessError: if scontrol exits non-zero.
        """
        nodelist = os.environ.get("SLURM_NODELIST")
        if not nodelist:
            raise RuntimeError(
                "SLURM_NODELIST is not set — are you running inside a Slurm allocation?"
            )
        result = subprocess.run(
            ["scontrol", "show", "hostnames", nodelist],
            stdout=subprocess.PIPE,
            text=True,
            check=True,
        )
        nodes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if not nodes:
            raise RuntimeError(f"`scontrol show hostnames {nodelist}` printed no hostname")
        return nodes

    def status(self, ids: list[str]) -> list[JobStatus]:
        """The state of each job id, in input order.

        Tries `squeue` first (active/pending jobs); for ids not in the queue, falls back to
        `sacct` (completed/purged). Unknown ids report state "UNKNOWN" (source "none") rather
        than failing the whole call.
        """
        states: dict[str, JobStatus] = {}

        if ids:
            try:
                out = self._run(["squeue", "-h", "-o", "%i|%T", "-j", ",".join(ids)])
                for line in out.splitlines():
                    jid, _, state = line.strip().partition("|")
                    if jid:
                        states[jid] = JobStatus(jid, state or "UNKNOWN", "squeue")
            except (FileNotFoundError, subprocess.CalledProcessError):
                pass

        for jid in ids:
            if jid in states:
                continue
            try:
                out = self._run(["sacct", "-j", jid, "-n", "-P", "-o", "JobID,State,ExitCode"])
            except (FileNotFoundError, subprocess.CalledProcessError):
                states[jid] = JobStatus(jid, "UNKNOWN", "none")
                continue
            found = None
            for line in out.splitlines():
                cols = line.split("|")
                # The primary job row has JobID exactly == jid (not jid.batch/.extern).
                if cols and cols[0].strip() == jid:
                    found = JobStatus(
                        jid,
                        cols[1].strip() if len(cols) > 1 else "UNKNOWN",
                        "sacct",
                        cols[2].strip() if len(cols) > 2 else None,
                    )
                    break
            states[jid] = found or JobStatus(jid, "UNKNOWN", "none")

        return [states[j] for j in ids]

    def cancel(self, job_id: str) -> CancelResult:
        """Cancel one job with `scancel`.

        A missing or already-terminal job reports `cancelled=False` with a detail hint rather
        than raising.
        """
        try:
            self._run(["scancel", job_id])
        except FileNotFoundError:
            return CancelResult(False, "scancel is not available on this host.")
        except subprocess.CalledProcessError as exc:
            return CancelResult(
                False, f"scancel exited {exc.returncode}; the job may already be gone."
            )
        return CancelResult(True, None)

    def describe_nodes(self) -> dict[str, Any]:
        """Partitions and node tokens from `sinfo`.

        Degrades to `available: False` (with a `note`) when `sinfo` is missing or fails, e.g. on
        a non-Slurm host.
        """
        result: dict[str, Any] = {
            "available": False,
            "partitions": [],
            "nodes": [],
        }

        try:
            part_out = self._run(["sinfo", "-h", "-o", "%R|%a|%D"])
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            result["note"] = f"sinfo unavailable: {type(exc).__name__}"
            return result

        result["available"] = True
        seen: set[str] = set()
        for line in part_out.splitlines():
            parts = line.split("|")
            if not parts or not parts[0].strip():
                continue
            name = parts[0].strip()
            if name in seen:
                continue
            seen.add(name)
            entry: dict[str, str | int] = {"name": name}
            if len(parts) > 1:
                entry["avail"] = parts[1].strip()
            if len(parts) > 2:
                try:
                    entry["nodes"] = int(parts[2].strip())
                except ValueError:
                    pass
            result["partitions"].append(entry)

        try:
            node_out = self._run(["sinfo", "-h", "-o", "%N"])
            tokens: list[str] = []
            for line in node_out.splitlines():
                for top in _split_nodelist(line.strip()):
                    tokens.extend(_expand_nodelist_token(top))
            result["nodes"] = tokens
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass  # partitions still useful without the node breakdown

        return result
