from __future__ import annotations

import json
import subprocess

import pytest

from core import auth_recover as ar

WORKSPACE_LIST = json.dumps(
    [{"workspaceId": "ws-1", "refreshToken": "rt-1", "refreshExpiresAt": 4102444800}]
)


def completed(
    stdout: str = "", returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess:
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


@pytest.fixture(autouse=True)
def chain_is_broken(monkeypatch: pytest.MonkeyPatch):
    """Default every test to 'headless chain is dead' so the ladder runs.

    recover() short-circuits when the existing chain still works, which is
    the right production behavior but would make driver tests vacuous.
    """
    monkeypatch.setattr(ar, "_default_precheck", lambda: {"status": "not_bootstrapped"})


@pytest.fixture(autouse=True)
def no_real_chrome_profiles(monkeypatch: pytest.MonkeyPatch, tmp_path_factory):
    """Never read the developer's actual Chrome LevelDB during tests.

    `auto` tries chrome-disk first, so without this the browser-driver tests
    would pass or fail depending on whether the machine happens to have a
    live web.plaud.ai session on disk.
    """
    monkeypatch.setattr(ar, "CHROME_PROFILES_DIR", tmp_path_factory.mktemp("no-chrome") / "absent")


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


def test_working_chain_short_circuits_before_any_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy headless chain must answer without spending a rotation."""
    monkeypatch.setattr(ar, "_default_precheck", lambda: {"status": "ok", "detail": "refreshed"})

    def explode(args):
        raise AssertionError("a browser driver was reached despite a healthy chain")

    outcome = ar.recover(runner=explode, bootstrap=explode)
    assert outcome.status == "ok"
    assert outcome.driver == "ws-refresh"


def test_force_bypasses_the_precheck(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ar, "_default_precheck", lambda: {"status": "ok"})
    monkeypatch.setattr(ar, "CHROME_PROFILES_DIR", make_profiles(tmp_path, "Default"))
    install_fake_leveldb(
        monkeypatch,
        {
            "Default": {
                "https://web.plaud.ai": [
                    FakeRecord("pld_a:workspaceList", workspace_json(4102444800))
                ]
            }
        },
    )
    outcome = ar.recover(force=True, bootstrap=lambda text: {"status": "ok"})
    assert outcome.driver == "chrome-disk"


def test_recover_unknown_driver() -> None:
    outcome = ar.recover(driver="safari")
    assert outcome.status == "driver_unavailable"


class FakeRecord:
    def __init__(self, script_key: str, value: str, seq: int = 0, live: bool = True) -> None:
        self.script_key = script_key
        self.value = value
        self.leveldb_seq_number = seq
        self.is_live = live


class FakeStore:
    """Stand-in for ccl_chromium_localstorage.LocalStoreDb."""

    def __init__(self, records: dict[str, list[FakeRecord]]) -> None:
        self._records = records
        self.closed = False

    def iter_storage_keys(self):
        return iter(self._records)

    def iter_records_for_storage_key(self, host: str, *, include_deletions=False):
        return iter(self._records.get(host, []))

    def close(self) -> None:
        self.closed = True


def install_fake_leveldb(
    monkeypatch: pytest.MonkeyPatch, per_profile: dict[str, dict[str, list[FakeRecord]]]
) -> None:
    """Fake the ccl_chromium_reader import keyed by profile directory name."""
    import sys
    import types

    module = types.ModuleType("ccl_chromium_reader.ccl_chromium_localstorage")

    def local_store_db(leveldb_path):
        profile = leveldb_path.parent.parent.name
        if profile not in per_profile:
            raise RuntimeError("no store here")
        return FakeStore(per_profile[profile])

    module.LocalStoreDb = local_store_db
    package = types.ModuleType("ccl_chromium_reader")
    package.ccl_chromium_localstorage = module
    monkeypatch.setitem(sys.modules, "ccl_chromium_reader", package)
    monkeypatch.setitem(sys.modules, "ccl_chromium_reader.ccl_chromium_localstorage", module)


def make_profiles(tmp_path, *names: str):
    for name in names:
        (tmp_path / name / "Local Storage" / "leveldb").mkdir(parents=True)
    return tmp_path


def workspace_json(expires: int, token: str = "rt-1") -> str:
    return json.dumps([{"workspaceId": "ws-1", "refreshToken": token, "refreshExpiresAt": expires}])


def test_chrome_disk_capture_success(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_profiles(tmp_path, "Default")
    install_fake_leveldb(
        monkeypatch,
        {
            "Default": {
                "https://web.plaud.ai": [
                    FakeRecord("pld_abc:workspaceList", workspace_json(4102444800))
                ]
            }
        },
    )
    value = ar.capture_via_chrome_disk(profiles_dir=tmp_path)
    assert json.loads(value)[0]["refreshToken"] == "rt-1"


def test_chrome_disk_uses_latest_revision_when_rotations_share_expiry(tmp_path, monkeypatch):
    make_profiles(tmp_path, "Default")
    install_fake_leveldb(
        monkeypatch,
        {
            "Default": {
                "https://web.plaud.ai": [
                    FakeRecord("pld_a:workspaceList", workspace_json(4102444800, "stale"), seq=1),
                    FakeRecord("pld_a:workspaceList", workspace_json(4102444800, "current"), seq=2),
                ]
            }
        },
    )
    assert (
        json.loads(ar.capture_via_chrome_disk(profiles_dir=tmp_path))[0]["refreshToken"]
        == "current"
    )


def test_chrome_disk_honors_latest_deletion_instead_of_resurrecting_session(tmp_path, monkeypatch):
    make_profiles(tmp_path, "Default")
    install_fake_leveldb(
        monkeypatch,
        {
            "Default": {
                "https://web.plaud.ai": [
                    FakeRecord("pld_a:workspaceList", workspace_json(4102444800, "deleted"), seq=1),
                    FakeRecord("pld_a:workspaceList", "", seq=2, live=False),
                ]
            }
        },
    )
    with pytest.raises(ar.RecoverError) as exc:
        ar.capture_via_chrome_disk(profiles_dir=tmp_path)
    assert exc.value.status == "no_session"


def test_recover_does_not_probe_browsers_during_network_outage():
    def unexpected(*args, **kwargs):
        raise AssertionError("browser recovery should not run for a network outage")

    outcome = ar.recover(
        precheck=lambda: {"status": "unreachable", "detail": "network unavailable"},
        runner=unexpected,
        bootstrap=unexpected,
    )
    assert outcome.status == "unreachable" and outcome.driver == "ws-refresh"


def test_chrome_disk_picks_freshest_across_profiles(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multiple profiles/rotations coexist — the latest expiry must win."""
    make_profiles(tmp_path, "Default", "Profile 1")
    install_fake_leveldb(
        monkeypatch,
        {
            "Default": {
                "https://web.plaud.ai": [
                    FakeRecord("pld_a:workspaceList", workspace_json(1000, "stale"))
                ]
            },
            "Profile 1": {
                "https://web.plaud.ai": [
                    FakeRecord("pld_b:workspaceList", workspace_json(9000, "fresh"))
                ]
            },
        },
    )
    value = ar.capture_via_chrome_disk(profiles_dir=tmp_path)
    assert json.loads(value)[0]["refreshToken"] == "fresh"


def test_chrome_disk_preserves_candidates_for_multiple_workspaces(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The current Keychain wid is selected later; global newest is unsafe."""
    make_profiles(tmp_path, "Default", "Profile 1")
    install_fake_leveldb(
        monkeypatch,
        {
            "Default": {
                "https://web.plaud.ai": [
                    FakeRecord(
                        "pld_a:workspaceList",
                        json.dumps(
                            [
                                {
                                    "workspaceId": "ws-current",
                                    "refreshToken": "current-token",
                                    "refreshExpiresAt": 1000,
                                }
                            ]
                        ),
                    )
                ]
            },
            "Profile 1": {
                "https://web.plaud.ai": [
                    FakeRecord(
                        "pld_b:workspaceList",
                        json.dumps(
                            [
                                {
                                    "workspaceId": "ws-other",
                                    "refreshToken": "other-token",
                                    "refreshExpiresAt": 9000,
                                }
                            ]
                        ),
                    )
                ]
            },
        },
    )

    entries = json.loads(ar.capture_via_chrome_disk(profiles_dir=tmp_path))

    assert {entry["workspaceId"] for entry in entries} == {"ws-current", "ws-other"}


def test_chrome_disk_ignores_other_hosts(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_profiles(tmp_path, "Default")
    install_fake_leveldb(
        monkeypatch,
        {
            "Default": {
                "https://example.com": [FakeRecord("pld_x:workspaceList", workspace_json(9999))]
            }
        },
    )
    with pytest.raises(ar.RecoverError) as exc:
        ar.capture_via_chrome_disk(profiles_dir=tmp_path)
    assert exc.value.status == "no_session"


def test_chrome_disk_missing_profile_dir(tmp_path) -> None:
    with pytest.raises(ar.RecoverError) as exc:
        ar.capture_via_chrome_disk(profiles_dir=tmp_path / "nope")
    assert exc.value.status == "driver_unavailable"


def test_auto_prefers_chrome_disk(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """chrome-disk is zero-touch, so auto must try it before any browser."""
    monkeypatch.setattr(ar, "CHROME_PROFILES_DIR", make_profiles(tmp_path, "Default"))
    install_fake_leveldb(
        monkeypatch,
        {
            "Default": {
                "https://web.plaud.ai": [
                    FakeRecord("pld_a:workspaceList", workspace_json(4102444800))
                ]
            }
        },
    )

    def explode(args):  # neither osascript nor cmux may be reached
        raise AssertionError(f"fell through to a browser driver: {args}")

    outcome = ar.recover(runner=explode, bootstrap=lambda text: {"status": "ok"})
    assert outcome.status == "ok"
    assert outcome.driver == "chrome-disk"


def test_stale_disk_token_falls_through_to_live_chrome(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chrome rotates its stored token; a rejected disk copy must not end the ladder."""
    monkeypatch.setattr(ar, "CHROME_PROFILES_DIR", make_profiles(tmp_path, "Default"))
    install_fake_leveldb(
        monkeypatch,
        {
            "Default": {
                "https://web.plaud.ai": [
                    FakeRecord("pld_a:workspaceList", workspace_json(4102444800, "stale"))
                ]
            }
        },
    )

    def osascript_only(args):
        assert args[0] == "osascript"  # cmux must never be reached
        return completed(workspace_json(4102444800, "live"))

    seen: list[str] = []

    def bootstrap(text: str):
        token = json.loads(text)[0]["refreshToken"]
        seen.append(token)
        return {"status": "ok"} if token == "live" else {"status": "rejected"}

    outcome = ar.recover(runner=osascript_only, bootstrap=bootstrap)
    assert outcome.status == "ok"
    assert outcome.driver == "chrome"
    assert seen == ["stale", "live"]


def test_unexpected_disk_permission_error_falls_through_to_live_chrome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """macOS TCC denial on Chrome's profile must not abort silent recovery."""
    monkeypatch.setattr(
        ar,
        "capture_via_chrome_disk",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("TCC denied")),
    )
    monkeypatch.setattr(ar, "capture_via_chrome", lambda **kwargs: WORKSPACE_LIST)

    outcome = ar.recover(bootstrap=lambda text: {"status": "ok"})

    assert outcome.status == "ok"
    assert outcome.driver == "chrome"


def test_earlier_chrome_diagnostic_is_not_masked_by_missing_cmux(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ar, "CHROME_PROFILES_DIR", tmp_path / "absent")
    monkeypatch.setattr(ar.shutil, "which", lambda name: None)  # no cmux either

    def dead(args):
        return completed(returncode=1, stderr="AppleScript is turned off")

    outcome = ar.recover(runner=dead)
    assert outcome.status == "driver_unavailable"
    assert outcome.driver == "chrome-disk"
    assert "Chrome profile dir not found" in outcome.detail
    assert "cmux=driver_unavailable" in outcome.detail


def test_prioritized_failure_summarizes_statuses_without_secondary_details() -> None:
    outcome = ar._prioritized_failure(
        [
            ar.RecoverOutcome(
                status="chrome_js_disabled",
                detail="Enable JavaScript from Apple Events",
                driver="chrome",
            ),
            ar.RecoverOutcome(
                status="driver_unavailable",
                detail="must-not-leak-secondary-detail",
                driver="cmux",
            ),
        ]
    )

    assert outcome.driver == "chrome"
    assert outcome.status == "chrome_js_disabled"
    assert "Enable JavaScript from Apple Events" in outcome.detail
    assert "cmux=driver_unavailable" in outcome.detail
    assert "must-not-leak-secondary-detail" not in outcome.detail


def test_auto_preserves_tcc_diagnostic_when_later_drivers_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(status: str, detail: str):
        def capture(**kwargs):
            raise ar.RecoverError(status, detail)

        return capture

    monkeypatch.setattr(
        ar,
        "capture_via_chrome_disk",
        fail("driver_unavailable", "Chrome localStorage is protected by macOS privacy permissions"),
    )
    monkeypatch.setattr(
        ar,
        "capture_via_chrome",
        fail("driver_unavailable", "Chrome AppleScript unavailable"),
    )
    monkeypatch.setattr(
        ar,
        "capture_via_cmux",
        fail("driver_unavailable", "cmux CLI not installed"),
    )

    outcome = ar.recover(bootstrap=lambda text: {"status": "ok"})

    assert outcome.driver == "chrome-disk"
    assert "protected by macOS privacy permissions" in outcome.detail
    assert "cmux CLI not installed" not in outcome.detail
    assert "cmux=driver_unavailable" in outcome.detail


class FakeChrome:
    """Scripted osascript runner for the Chrome driver."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.result = completed(stdout, returncode=returncode, stderr=stderr)
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(args)
        assert args[0] == "osascript"
        return self.result


def test_chrome_capture_success() -> None:
    runner = FakeChrome(stdout=WORKSPACE_LIST + "\n")
    assert json.loads(ar.capture_via_chrome(runner=runner))[0]["workspaceId"] == "ws-1"


def test_chrome_script_creates_a_window_before_opening_tab_when_none_exist() -> None:
    runner = FakeChrome(stdout=WORKSPACE_LIST)

    ar.capture_via_chrome(runner=runner)

    script = runner.calls[0][2]
    zero_window_guard = "if (count of windows) is 0 then"
    create_window = "set targetWindow to make new window"
    create_tab = "make new tab at end of tabs of targetWindow"
    assert zero_window_guard in script
    assert create_window in script
    assert create_tab in script
    assert script.index(zero_window_guard) < script.index(create_window) < script.index(create_tab)


def test_chrome_js_toggle_disabled() -> None:
    runner = FakeChrome(
        returncode=1,
        stderr="execution error: Executing JavaScript through AppleScript is turned off. (12)",
    )
    with pytest.raises(ar.RecoverError) as exc:
        ar.capture_via_chrome(runner=runner)
    assert exc.value.status == "chrome_js_disabled"


def test_chrome_applescript_error_keeps_only_numeric_code() -> None:
    runner = FakeChrome(
        returncode=1,
        stderr="execution error: browser-storage-sensitive-text. Invalid index. (-1719)",
    )

    with pytest.raises(ar.RecoverError) as exc:
        ar.capture_via_chrome(runner=runner)

    assert exc.value.status == "driver_unavailable"
    assert exc.value.detail == "Chrome AppleScript failed (error -1719)"
    assert "sensitive" not in exc.value.detail


def test_chrome_no_session() -> None:
    runner = FakeChrome(stdout="missing value")
    with pytest.raises(ar.RecoverError) as exc:
        ar.capture_via_chrome(runner=runner)
    assert exc.value.status == "no_session"


def test_auto_falls_through_chrome_to_cmux() -> None:
    """When Chrome can't help, auto continues to the cmux driver."""

    class Router:
        def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
            if args[0] == "osascript":
                return completed(returncode=1, stderr="AppleScript is turned off")
            if "open" in args:
                return completed("opened surface:1")
            if "wait" in args:
                return completed("")
            if "eval" in args:
                return completed(WORKSPACE_LIST)
            raise AssertionError(args)

    outcome = ar.recover(
        runner=Router(), bootstrap=lambda text: {"status": "ok", "detail": "armed"}
    )
    assert outcome.status == "ok"
    assert outcome.driver == "cmux"


def test_chrome_driver_reports_chrome_in_outcome() -> None:
    runner = FakeChrome(stdout=WORKSPACE_LIST)
    outcome = ar.recover(driver="chrome", runner=runner, bootstrap=lambda text: {"status": "ok"})
    assert outcome.status == "ok"
    assert outcome.driver == "chrome"
