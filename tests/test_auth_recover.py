from __future__ import annotations

import json
import subprocess

import pytest

from core import auth_recover as ar

WORKSPACE_LIST = json.dumps(
    [{"workspaceId": "ws-1", "refreshToken": "rt-1", "refreshExpiresAt": 4102444800}]
)


def completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeCmux:
    """Scripted runner: maps the subcommand ('open'/'wait'/'eval') to a result."""

    def __init__(self, *, eval_stdout: str, open_rc: int = 0) -> None:
        self.eval_stdout = eval_stdout
        self.open_rc = open_rc
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(args)
        if "open" in args:
            return completed("opened surface:3 (browser)", returncode=self.open_rc)
        if "wait" in args:
            return completed("")
        if "eval" in args:
            return completed(self.eval_stdout)
        raise AssertionError(f"unexpected cmux call: {args}")


@pytest.fixture(autouse=True)
def cmux_on_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ar.shutil, "which", lambda name: "/usr/local/bin/cmux")


def test_capture_success_targets_opened_surface() -> None:
    runner = FakeCmux(eval_stdout=WORKSPACE_LIST + "\n")
    value = ar.capture_via_cmux(runner=runner, poll_seconds=0, sleep=lambda s: None)
    assert json.loads(value)[0]["workspaceId"] == "ws-1"
    eval_call = next(c for c in runner.calls if "eval" in c)
    assert "surface:3" in eval_call


def test_capture_no_session_raises_after_polling() -> None:
    runner = FakeCmux(eval_stdout='""')
    with pytest.raises(ar.RecoverError) as exc:
        ar.capture_via_cmux(runner=runner, poll_seconds=0, sleep=lambda s: None)
    assert exc.value.status == "no_session"


def test_capture_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ar.shutil, "which", lambda name: None)
    with pytest.raises(ar.RecoverError) as exc:
        ar.capture_via_cmux()
    assert exc.value.status == "driver_unavailable"


def test_recover_ok_pipes_capture_into_bootstrap() -> None:
    runner = FakeCmux(eval_stdout=WORKSPACE_LIST)
    seen: dict[str, str] = {}

    def fake_bootstrap(text: str):
        seen["text"] = text
        return {"status": "ok", "detail": "refreshed"}

    outcome = ar.recover(runner=runner, bootstrap=fake_bootstrap)
    assert outcome.status == "ok"
    assert json.loads(seen["text"]) == json.loads(WORKSPACE_LIST)


def test_recover_surfaces_bootstrap_failure() -> None:
    runner = FakeCmux(eval_stdout=WORKSPACE_LIST)
    outcome = ar.recover(
        runner=runner,
        bootstrap=lambda text: {"status": "rejected", "detail": "server said no"},
    )
    assert outcome.status == "bootstrap_rejected"
    assert outcome.bootstrap["detail"] == "server said no"


def test_recover_rejects_garbage_capture() -> None:
    runner = FakeCmux(eval_stdout="<!doctype html>")
    outcome = ar.recover(runner=runner, bootstrap=lambda text: {"status": "ok"})
    assert outcome.status == "capture_failed"


def test_recover_unknown_driver() -> None:
    outcome = ar.recover(driver="safari")
    assert outcome.status == "driver_unavailable"
