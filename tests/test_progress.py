"""The shared progress contract: a stage names what is happening and what it waits for."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from crab.cli.progress import (
    NullReporter,
    Stage,
    current_reporter,
    stage,
    use_reporter,
)


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, bool | None]] = []
        self.begun: list[Stage] = []

    def begin(self, s: Stage) -> None:
        self.begun.append(s)
        self.events.append(("begin", s.stage, None))

    def end(self, stage_id: str, ok: bool) -> None:
        self.events.append(("end", stage_id, ok))


def test_stage_is_a_frozen_record_with_an_optional_limit():
    s = Stage("ssh-connect", "Connecting", "the login node", 100.0)
    assert (s.stage, s.label, s.waiting_for, s.started_at, s.timeout_s) == (
        "ssh-connect",
        "Connecting",
        "the login node",
        100.0,
        None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.label = "x"  # type: ignore[misc]


def test_default_reporter_is_silent_and_the_body_still_runs():
    assert isinstance(current_reporter(), NullReporter)
    ran = []
    with stage("install", "Installing", "pip"):
        ran.append(1)
    assert ran == [1]


def test_stage_begins_and_ends_with_the_injected_clock():
    rec = Recorder()
    with use_reporter(rec):
        with stage("ssh-connect", "Connecting", "the login node", 30, clock=lambda: 42.5):
            assert rec.events == [("begin", "ssh-connect", None)]
    assert rec.begun == [Stage("ssh-connect", "Connecting", "the login node", 42.5, 30)]
    assert rec.events[-1] == ("end", "ssh-connect", True)


def test_stage_ends_not_ok_when_the_body_raises_and_the_error_propagates():
    rec = Recorder()
    with use_reporter(rec), pytest.raises(ValueError):
        with stage("crab-check", "Checking", "crab info"):
            raise ValueError("boom")
    assert rec.events == [("begin", "crab-check", None), ("end", "crab-check", False)]


def test_nested_stages_end_inside_out():
    rec = Recorder()
    with use_reporter(rec):
        with stage("install", "Installing", "pip"):
            with stage("verify", "Verifying", "crab info"):
                pass
    assert [(k, i) for k, i, _ in rec.events] == [
        ("begin", "install"),
        ("begin", "verify"),
        ("end", "verify"),
        ("end", "install"),
    ]


def test_use_reporter_restores_the_previous_reporter():
    outer, inner = Recorder(), Recorder()
    with use_reporter(outer):
        with use_reporter(inner):
            assert current_reporter() is inner
        assert current_reporter() is outer
    assert isinstance(current_reporter(), NullReporter)


def test_concurrent_tasks_each_report_to_the_reporter_they_inherited():
    a, b = Recorder(), Recorder()

    async def work(name: str) -> None:
        with stage(name, name, "x"):
            await asyncio.sleep(0)

    async def run(rep: Recorder, name: str) -> None:
        with use_reporter(rep):
            # create_task copies the context, as the background tasks of the web layer do
            await asyncio.create_task(work(name))

    async def main() -> None:
        await asyncio.gather(run(a, "one"), run(b, "two"))

    asyncio.run(main())
    assert [i for _, i, _ in a.events] == ["one", "one"]
    assert [i for _, i, _ in b.events] == ["two", "two"]
