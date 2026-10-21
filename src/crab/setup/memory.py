from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Any

from crab import paths

CRAB_ROOT = str(paths.crab_home())
CONFIG_DIR = os.path.join(CRAB_ROOT, "config")
# Receipts are per-machine, so they live in the git-ignored local/ folder. Receipts written
# before that folder existed stay readable from LEGACY_ENV_DIR; a local receipt wins.
ENV_DIR = str(paths.local_path(Path(CRAB_ROOT), "receipts"))
LEGACY_ENV_DIR = os.path.join(CONFIG_DIR, "environments")


class ReceiptError(Exception):
    """A receipt file exists but cannot be read or parsed."""


def ensure_env_dir():
    """Ensures the environments directory exists."""
    os.makedirs(ENV_DIR, exist_ok=True)


def save_receipt(benchmark_id: str, receipt: dict[str, Any]):
    """Saves a rich environment receipt for a benchmark (atomic write)."""
    ensure_env_dir()
    receipt_file = os.path.join(ENV_DIR, f"{benchmark_id}.json")
    tmp_file = receipt_file + ".tmp"

    with open(tmp_file, "w") as f:
        json.dump(receipt, f, indent=4)
    os.replace(tmp_file, receipt_file)


def _receipt_file(benchmark_id: str) -> str | None:
    """The receipt file for an id: the local one if present, else a legacy one, else None."""
    for directory in (ENV_DIR, LEGACY_ENV_DIR):
        candidate = os.path.join(directory, f"{benchmark_id}.json")
        if os.path.exists(candidate):
            return candidate
    return None


def read_receipt_file(receipt_file: str | os.PathLike[str]) -> dict[str, Any]:
    """Parses one receipt file.

    Raises:
        ReceiptError: if the file cannot be read, is not valid JSON, or is not a JSON object.
    """
    try:
        with open(receipt_file) as f:
            receipt = json.load(f)
    except json.JSONDecodeError as exc:
        raise ReceiptError(
            f"receipt {receipt_file} is not valid JSON ({exc}). "
            "Fix or delete the file, or register the binary again."
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        raise ReceiptError(f"cannot read receipt {receipt_file}: {reason}") from exc
    if not isinstance(receipt, dict):
        raise ReceiptError(
            f"receipt {receipt_file} must hold a JSON object, got {type(receipt).__name__}"
        )
    return receipt


def get_receipt(benchmark_id: str) -> dict[str, Any] | None:
    """Loads a benchmark receipt. Returns None if there is none (the binary lookup then falls
    through to the config and PATH).

    Raises:
        ReceiptError: if a receipt file exists but cannot be read or parsed. The file is left
            as it is.
    """
    receipt_file = _receipt_file(benchmark_id)
    if receipt_file is None:
        return None
    return read_receipt_file(receipt_file)


def remove_receipt(benchmark_id: str):
    """Safely removes a benchmark receipt (both the local and any legacy copy)."""
    for directory in (ENV_DIR, LEGACY_ENV_DIR):
        try:
            os.remove(os.path.join(directory, f"{benchmark_id}.json"))
        except FileNotFoundError:
            pass


def get_all_receipts() -> dict[str, dict[str, Any]]:
    """Returns a dictionary of all configured benchmarks and their receipts."""
    ensure_env_dir()
    receipts = {}

    ids = {
        Path(file).stem
        for directory in (ENV_DIR, LEGACY_ENV_DIR)
        for file in glob.glob(os.path.join(directory, "*.json"))
    }
    for bench_id in sorted(ids):
        receipt = get_receipt(bench_id)
        if receipt:
            receipts[bench_id] = receipt

    return receipts
