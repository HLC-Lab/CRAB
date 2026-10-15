"""Experiment names of the NPLB campaign, shared by the config generator and the analysis.

A name is `<phase>_<test>_<net>_P<paths>`: fields are joined with `_`, and a field never contains
one (it uses `-`). Examples:

    p0_cross_C0_P1                          phase 0, cross-spine ping-pong
    p1_perm_C2_P4                           phase 1, permutation alone
    p2_perm_alone_C0_P4                     phase 2, victim alone
    p2_perm_vs_burst-0.01-0.0001_C2_P1      phase 2, victim against a bursty aggressor
    cong_burst-0.01-0.0001_C0_P1            colleague's set (the file says which victim)
    pp_n1n3-md_C2_P1                        ping-pong set: nodes 1 and 3, the md port variant
"""

from __future__ import annotations

import re

P0_TESTS = ("cross", "local")
P1_TESTS = ("perm", "a2a", "ag")
VICTIMS = ("perm", "a2a", "ag")
_BURST = r"burst-[0-9.]+-[0-9.]+"
_AGGRESSOR = rf"(?:a2a|incast|pw|{_BURST})"
_NAME = re.compile(r"^(?P<phase>[a-z0-9]+)_(?P<test>.+)_(?P<net>C\d+)_P(?P<paths>\d+)$")
_P2_TEST = re.compile(rf"^(?P<victim>perm|a2a|ag)_(?:alone|vs_(?P<aggressor>{_AGGRESSOR}))$")
_CONG_TEST = re.compile(rf"^(?P<aggressor>{_BURST})$")
_PP_TEST = re.compile(r"^n\d+n\d+-(?:md|default|dual|pw8)$")


def experiment_name(phase: str, test: str, net: str, paths: int) -> str:
    """The experiment name for one phase, test, network config (`C0`...) and path count."""
    return f"{phase}_{test}_{net}_P{paths}"


def parse_experiment_name(name: str) -> dict[str, str | int | None] | None:
    """Split a generated name into its fields, or None when it is not one of ours.

    Keys: phase, test, victim, aggressor, net, paths. `victim` is the victim of phase 2, the test
    itself in phases 0, 1 and `pp`, and None in the colleague set (`cong`), where the file name
    carries it. `aggressor` is None for `*_alone` and outside phase 2 and `cong`.
    """
    match = _NAME.match(name)
    if match is None:
        return None
    phase, test = match["phase"], match["test"]
    victim: str | None
    aggressor: str | None = None
    if phase == "p0" and test in P0_TESTS:
        victim = test
    elif phase == "p1" and test in P1_TESTS:
        victim = test
    elif phase == "p2" and (p2 := _P2_TEST.match(test)):
        victim, aggressor = p2["victim"], p2["aggressor"]
    elif phase == "pp" and _PP_TEST.match(test):
        victim = test
    elif phase == "cong" and (cong := _CONG_TEST.match(test)):
        victim, aggressor = None, cong["aggressor"]
    else:
        return None
    return {
        "phase": phase,
        "test": test,
        "victim": victim,
        "aggressor": aggressor,
        "net": match["net"],
        "paths": int(match["paths"]),
    }
