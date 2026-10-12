from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_auto_refresh(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Keep the load_config auto-refresh hook inert during tests.

    A fixture that plants PLAUD_WS_REFRESH_TOKEN next to an expired
    PLAUD_AUTHORIZATION would otherwise make load_config fire a real network
    POST. Tests that exercise the hook re-enable it explicitly with mocks.
    """
    monkeypatch.setenv("PLAUD_AUTO_REFRESH", "0")
    # Production defaults to macOS Keychain with no plaintext fallback. Tests
    # use isolated temp .env files and must never touch the developer Keychain.
    monkeypatch.setenv("PLAUD_SECRET_STORE", "test-file")
    # API/refresh rejection tests persist a small server-verdict memo. Keep it
    # beside the test's isolated credentials, never in the live app data dir.
    import core.auth_status as auth_mod

    monkeypatch.setattr(auth_mod, "REJECTION_FILE", tmp_path / "auth_state.json")


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never spawn a vendor CLI or call a provider API from the test suite.

    Tests that need a model stub `run_model` (or `model_available`) at the
    module they exercise. Anything that slips through fails fast here instead
    of silently burning the developer's Claude/ChatGPT/Grok subscription —
    which is exactly what happened before this guard (pytest hung on a real
    `codex exec`).
    """
    import core.summarize as summarize

    def _blocked_run(argv, *_args, **_kwargs):
        raise summarize.ModelFailed(f"real CLI call blocked in tests: {argv[:2]}")

    def _blocked_post(url, *_args, **_kwargs):
        raise summarize.ModelFailed(f"real API call blocked in tests: {url}")

    monkeypatch.setattr(summarize.subprocess, "run", _blocked_run)
    monkeypatch.setattr(summarize.httpx, "post", _blocked_post)


@pytest.fixture(autouse=True)
def _isolated_vault_index(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The vault index never opens data/plaud.db, and never walks a vault by
    itself: refresh_if_stale indexes every configured vault, which outside a
    test's own setup would be the developer's real one. Tests that exercise
    either set them up explicitly."""
    import core.vault_index as vault_index

    monkeypatch.setattr(vault_index, "DEFAULT_DB", tmp_path / "vault-index.db")
    monkeypatch.setattr(vault_index, "refresh_if_stale", lambda **_k: None)


@pytest.fixture()
def index_note():
    """Write `root/rel` and index it, as vault `root.name`, in the test's
    own (tmp) vault index."""
    import core.vault_index as vault_index

    def add(root, rel: str, *, note_type: str | None = None):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        conn = vault_index._connect()
        try:
            conn.execute(
                "INSERT INTO vault_notes (vault, path, rel_path, title, aliases, tags,"
                " description, type, mtime, indexed_at)"
                " VALUES (?, ?, ?, ?, '[]', '[]', NULL, ?, 0, 0)",
                (root.name, str(path), rel, path.stem, note_type),
            )
            conn.commit()
        finally:
            conn.close()
        return path

    return add


@pytest.fixture(autouse=True)
def _shipped_taxonomy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Classify against the shipped DEFAULT_TAXONOMY, never the developer's
    gitignored data/classification.json (otherwise test outcomes depend on
    whichever personal folder set happens to be on this machine)."""
    import core.classification as classification

    monkeypatch.setattr(classification, "load_taxonomy", lambda: classification.DEFAULT_TAXONOMY)
    monkeypatch.setattr(classification, "_taxonomy_cache", None)
