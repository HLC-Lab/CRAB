"""Copying the extra files a wrapper asks to keep into the experiment's results.

`get_extra_artifacts()` returns paths or glob patterns relative to the app's run directory.
Matches are copied to `<exp_dir>/artifacts/run_<n>/app_<id>/`, keeping their relative layout,
for every run (a failed run's logs are often the most useful ones). A pattern that matches
nothing, or points outside the run directory, is logged and skipped: artifacts are extras and
never fail a run.
"""

from __future__ import annotations

import glob
import os
import shutil
from typing import Any


def copy_artifacts(apps: list[Any], exp_dir: str, run_id: int, log: Any) -> None:
    for app in apps:
        run_dir = getattr(app, "run_dir", None)
        get_patterns = getattr(app, "get_extra_artifacts", None)
        if not run_dir or get_patterns is None:
            continue
        root = os.path.realpath(run_dir)
        dest_root = os.path.join(exp_dir, "artifacts", f"run_{run_id}", f"app_{app.id_num}")

        for pattern in get_patterns() or []:
            if os.path.isabs(pattern):
                log.warning(
                    f"app {app.id_num}: artifact {pattern!r} must be relative to its run dir"
                )
                continue
            matches = sorted(glob.glob(os.path.join(root, pattern)))
            inside = [m for m in matches if os.path.realpath(m).startswith(root + os.sep)]
            if not inside:
                log.warning(f"app {app.id_num}: artifact {pattern!r} not found in run {run_id}")
                continue
            for source in inside:
                target = os.path.join(dest_root, os.path.relpath(source, root))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.isdir(source):
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(source, target)
