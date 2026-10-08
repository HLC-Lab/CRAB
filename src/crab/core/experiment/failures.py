"""Writing the per-app error log of a failed run."""

import os
from typing import Any


def write_error_log(
    exp_dir: str, aid: int, exit_code: int, stdout_text: str, stderr_text: str, app_log: Any
) -> None:
    try:
        err_path = os.path.join(exp_dir, f"error_app_{aid}.log")
        with open(err_path, "w") as f:
            f.write(f"App {aid} exit={exit_code}\n")
            if stdout_text.strip():
                f.write(f"\n--- STDOUT ---\n{stdout_text}\n")
            if stderr_text and stderr_text.strip():
                f.write(f"\n--- STDERR ---\n{stderr_text}\n")
    except Exception:
        app_log.warning("Could not write error log file")
