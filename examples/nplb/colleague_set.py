"""The colleague's congestion tests, adapted to the campaign hardware (see make_configs.py).

One experiment per burst setting: the victim's sizes chained on the victim partition, a bursty
all-to-all on the aggressor partition. Runs on C0 and C2 only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from naming import experiment_name

if TYPE_CHECKING:
    from make_configs import Built, Settings

MIB = 1 << 20
COLLEAGUE_NETS = ("C0", "C2")
COLLEAGUE_SIZES = [8, 64, 512, 4096, 32768, 262144, 2 * MIB, 16 * MIB]
COLLEAGUE = {
    "a2a": "blink/alltoall/alltoall_comm_only.py",
    "agtr": "blink/allgather/allgather_comm_only.py",
    "ardc": "blink/allreduce/allreduce_nb.py",
}
COLLEAGUE_WHAT = {
    "a2a": "all-to-all",
    "agtr": "all-gather",
    "ardc": "all-reduce",
}


def build_colleague(cfg: Settings, stem: str, net: str, paths: int, key: str) -> Built:
    """The colleague's congestion test: one experiment per burst setting, sizes chained."""
    out, where = cfg.collector(), cfg.placement()
    sizes = cfg.sizes(COLLEAGUE_SIZES)
    for length, pause in cfg.burst_grid():
        apps, _ = cfg.chain(
            COLLEAGUE[key], "", sizes, where, paths, head_start="0", partition="victim"
        )
        args = f"-endl -msgsize {cfg.aggressor_size} -blength {length} -bpause {pause}"
        apps[str(len(apps))] = cfg.app(
            "blink/alltoall/alltoall_man.py",
            args,
            where,
            paths,
            start="0",
            end="f",
            collect=False,
            partition="aggressor",
        )
        text = (
            f"{COLLEAGUE_WHAT[key].capitalize()} over a size sweep on the victim nodes, while "
            f"a bursty all-to-all of 2 MiB (bursts of {length} s, {pause} s apart) runs on "
            "the aggressor nodes."
        )
        out.add(experiment_name("cong", f"burst-{length}-{pause}", net, paths), text, apps, [])
    return out.built(cfg.config(stem, net, "colleague", cfg.partitions("interleaved")))


def build_colleague_files(cfg: Settings, prefix: str, net: str, paths: int) -> dict[str, Built]:
    """The three colleague files of one network config and path count: {file stem: Built}."""
    files: dict[str, Built] = {}
    for key in COLLEAGUE:
        stem = f"{prefix}col_{key}_{net}_P{paths}"
        files[stem] = build_colleague(cfg, stem, net, paths, key)
    return files
