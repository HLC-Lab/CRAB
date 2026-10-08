"""Slurm: the `#SBATCH` header, the job script, `sbatch`, and the worker's node list."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from typing import Any

from crab.log import CrabLogger


class SlurmScheduler:
    """Submits CRAB jobs to Slurm.

    Args:
        logger: receives the submit messages and the header warnings.
        crab_root: the checkout root; its `.venv/bin/activate`, when present, is sourced by the
            job script.
    """

    def __init__(self, logger: CrabLogger, crab_root: str) -> None:
        self.log = logger
        self.crab_root = crab_root

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
        cmd = (
            f"{shlex.quote(sys.executable)} "
            f"{shlex.quote(os.path.abspath(sys.argv[0]))} "
            f"worker --workdir {shlex.quote(job_dir)}"
        )

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
