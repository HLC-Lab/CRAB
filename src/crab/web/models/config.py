"""The experiment-config JSON shape, as the engine actually accepts it.

Single Python source of truth for what a config looks like (the engine itself
reads raw dicts; see ADR-015). Used to WARN, never to reject: configs are
hand-editable files and the cluster engine stays the final authority, so
``validate_config`` returns human-readable warnings instead of raising.

Ground truth for these shapes is ``examples/**/*.json`` plus the engine readers
(``core/engine.py``, ``core/experiment/runner.py``, ``core/allocation/``): every
example must validate warning-free (enforced by tests/test_web_config_model.py).

Deliberate permissiveness:
* ``extra="allow"`` everywhere — unknown app keys are wrapper attributes (a
  feature), and new engine options must not break older dashboards.
* Numeric options accept str|int|float — examples write numbers as strings.
* The legacy top-level ``applications`` key is accepted (engine rewrites it).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError

_Num = str | int | float


class _Permissive(BaseModel):
    model_config = ConfigDict(extra="allow")


class PartitionModel(_Permissive):
    share: _Num | None = None
    # Inner mode/split/etc. pass through via extra="allow".


class AllocationModel(_Permissive):
    mode: str | None = None
    split: list[_Num] | None = None
    stride: _Num | None = None
    seed: _Num | None = None
    partitions: dict[str, PartitionModel] | None = None


class OptionsModel(_Permissive):
    """Keys legal in both global_options and local_options."""

    minruns: _Num | None = None
    maxruns: _Num | None = None
    timeout: _Num | None = None
    convergeall: bool | None = None
    alpha: _Num | None = None
    beta: _Num | None = None
    outformat: str | None = None
    retain_files: bool | None = None
    tags: str | None = None
    extrainfo: str | None = None
    walltime: str | None = None
    datapath: str | None = None
    allocation: AllocationModel | None = None
    sbatch_directives: list[str] | dict[str, str | bool | int | float] | None = None


class GlobalOptionsModel(OptionsModel):
    name: str | None = None
    numnodes: _Num | None = None
    ppn: _Num | None = None


class AppModel(_Permissive):
    path: str | None = None
    args: str | None = None
    collect: bool | None = None
    start: _Num | None = None
    end: _Num | None = None
    partition: str | None = None
    # Anything else is a wrapper attribute, injected onto the wrapper instance.


class ExperimentModel(_Permissive):
    description: str | None = None
    apps: dict[str, AppModel] = {}
    local_options: OptionsModel | None = None


class CrabConfigModel(_Permissive):
    global_options: GlobalOptionsModel = GlobalOptionsModel()
    experiments: dict[str, ExperimentModel] = {}
    # Legacy single-experiment form; the engine rewrites it into experiments.
    applications: dict[str, AppModel] | None = None


def validate_config(config: object) -> list[str]:
    """Shape warnings for a config, empty when it looks engine-ready.

    Warnings, not errors: the caller saves the config regardless (it may be a
    work in progress or use engine features newer than this model).
    """
    if not isinstance(config, dict):
        return ["config is not a JSON object"]
    try:
        CrabConfigModel.model_validate(config)
    except ValidationError as exc:
        out = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "config"
            out.append(f"{loc}: {err['msg']}")
        return out
    return []
