"""Where the QE programs are, from the receipt the pw and ph wrappers share (qe-v6, qe-v7).

The receipt points at pw.x. ph.x is expected next to it.
"""

import os

from crab.wrappers.base import MissingBinaryError


def pw_from_receipt(receipt):
    """pw.x as the receipt gives it: a path, or a command name for a module install.

    None when there is no receipt or it names nothing.
    """
    if not receipt:
        return None
    base_path = receipt.get("binary_path", "")
    if not base_path:
        return None
    install_type = receipt.get("type", "source")

    if install_type == "module":
        return base_path

    if install_type == "binary":
        return os.path.join(base_path, "pw.x")

    # source: binary_path = target_dir/bin (CMake install prefix stub)
    # actual build output is at target_dir/build/bin/pw.x
    if base_path.endswith("bin"):
        base_path = os.path.dirname(base_path)
    return os.path.join(base_path, "build", "bin", "pw.x")


def ph_next_to(pw, benchmark_id):
    """ph.x in pw.x's directory, or the bare command `ph.x` when pw.x is a command name.

    Raises:
        MissingBinaryError: pw.x is a path and there is no ph.x beside it.
    """
    if not pw:
        return None
    directory = os.path.dirname(pw)
    if not directory:
        return "ph.x"
    ph = os.path.join(directory, "ph.x")
    if not os.path.isfile(ph):
        raise MissingBinaryError(
            f"No ph.x at {ph}, the directory of pw.x from receipt {benchmark_id!r} ({pw}). "
            "Install ph.x there, or set the app's 'binary' key in the config."
        )
    return ph
