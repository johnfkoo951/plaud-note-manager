"""Execute the shipped recovery JS with local stubs; never contact Plaud."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
from subprocess import run as run_local_javascript

import pytest


SOURCE = Path(__file__).parents[1] / "app/Sources/PlaudNoteApp/PlaudWebLoginScript.swift"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is needed for the shipped JavaScript")


def shipped_script(name: str) -> str:
    match = re.search(rf'let {name} = #"""\n(.*?)\n"""#', SOURCE.read_text(), re.S)
    assert match is not None
    return match.group(1)


HARNESS = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [], messages = [];
const storage = new Map(Object.entries({
  pld_userId: 'test-account', pld_USER_TAG: 'test-device',
  'pld_test-account:currentWorkspaceId': 'test-workspace',
  'pld_test-account:workspaceList': JSON.stringify([{
    workspaceId: 'test-workspace', domain: input.domain || 'https://api.plaud.ai',
    refreshToken: 'previous-refresh', refreshExpiresAt: 9999999999999,
    refresh_expires_at: 9999999999999
  }])
}));
const context = {
  URL, Headers, AbortController, Intl, Date, Promise,
  setTimeout: (callback) => queueMicrotask(callback),
  __plaudNativeCaptureGeneration: 'test-generation',
  location: { href: 'https://web.plaud.ai/' },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value)
  },
  webkit: { messageHandlers: { plaudAuthCapture: {
    postMessage: (message) => messages.push(message)
  }}},
  fetch: async (url, options) => {
    calls.push({url, redirect: options?.redirect});
    if (input.invalidate) context.__plaudNativeCaptureGeneration = 'replacement';
    const next = input.responses[calls.length - 1];
    if (!next || next.error) throw new Error('network_unavailable');
    return { status: next.http, ok: next.http >= 200 && next.http < 300,
             json: async () => next.body || {} };
  }
};
context.window = context;
(async () => {
  const result = await vm.runInNewContext(input.script, context);
  process.stdout.write(JSON.stringify({result, calls, messages,
    stored: JSON.parse(storage.get('pld_test-account:workspaceList'))}));
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""


def run_recovery(responses: list[dict], **options) -> dict:
    # Keep the suite's vendor-CLI guard intact. This import-bound runner only
    # executes the local Node harness whose fetch is replaced by test stubs.
    completed = run_local_javascript(
        [NODE, "-e", HARNESS],
        input=json.dumps(
            {
                "script": shipped_script("plaudAccountSessionRecoveryScript"),
                "responses": responses,
                **options,
            }
        ),
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    return json.loads(completed.stdout)


def pair_response() -> dict:
    return {
        "http": 200,
        "body": {
            "status": 0,
            "data": {
                "workspace_token": "fresh-access",
                "refresh_token": "fresh-refresh",
                "expires_in": 86400,
            },
        },
    }


def test_fresh_pair_uses_explicit_message_and_does_not_inherit_old_refresh_expiry():
    result = run_recovery([pair_response()])
    capture = next(item for item in result["messages"] if item["kind"] == "recoveryCapture")
    assert capture["generation"] == "test-generation"
    assert capture["headers"]["authorization"] == "bearer fresh-access"
    entry = json.loads(capture["workspaceList"])[0]
    assert entry["refreshToken"] == "fresh-refresh"
    assert entry["refreshExpiresAt"] is None
    assert "refresh_expires_at" not in entry
    assert result["calls"][0]["redirect"] == "error"


@pytest.mark.parametrize("response", [{"http": 503}, {"error": "offline"}])
def test_workspace_outage_defers_without_touching_account_refresh(response):
    result = run_recovery([response])
    assert result["result"]["status"] == "deferred"
    assert len(result["calls"]) == 1
    assert not any(item["kind"] == "recoveryCapture" for item in result["messages"])


@pytest.mark.parametrize("response", [{"http": 503}, {"error": "offline"}])
def test_account_refresh_outage_does_not_claim_session_expired(response):
    result = run_recovery([{"http": 401}, response])
    assert result["result"]["status"] == "deferred"
    assert len(result["calls"]) == 2


def test_exhausted_account_refresh_conflicts_defer():
    result = run_recovery([{"http": 401}] + [{"http": 409, "body": {"status": -4302}}] * 3)
    assert result["result"] == {"status": "deferred", "detail": "account_refresh_busy"}
    assert len(result["calls"]) == 4


def test_only_explicit_account_auth_rejection_requests_login():
    result = run_recovery([{"http": 401}, {"http": 403}])
    assert result["result"]["status"] == "needsLogin"


@pytest.mark.parametrize(
    "domain",
    [
        "https://outside.invalid",
        "http://api.plaud.ai",
        "https://api.plaud.ai.evil.invalid",
        "https://api.plaud.ai@evil.invalid",
        "https://api.plaud.ai:444",
        "https://api.plaud.ai/path",
    ],
)
def test_storage_domain_cannot_redirect_session_requests(domain):
    result = run_recovery([pair_response()], domain=domain)
    assert result["result"]["status"] == "deferred"
    assert result["calls"] == []


def test_server_region_hint_must_also_be_a_trusted_api_domain():
    result = run_recovery(
        [
            {
                "http": 200,
                "body": {
                    "status": -302,
                    "data": {"domains": {"api": "https://outside.invalid"}},
                },
            }
        ]
    )
    assert result["result"]["status"] == "deferred"
    assert len(result["calls"]) == 1


def test_disposed_generation_does_not_persist_or_emit_delayed_pair():
    result = run_recovery([pair_response()], invalidate=True)
    assert result["result"]["status"] == "cancelled"
    assert result["stored"][0]["refreshToken"] == "previous-refresh"
    assert not any(item["kind"] == "recoveryCapture" for item in result["messages"])


def test_request_hook_posts_only_trusted_https_api_and_current_generation():
    harness = r"""
const vm = require('node:vm'), fs = require('node:fs');
const messages = [], script = fs.readFileSync(0, 'utf8');
const context = { URL, Headers, __plaudNativeCaptureGeneration: 'current',
  location: {href: 'https://web.plaud.ai/'}, fetch: async () => ({}),
  XMLHttpRequest: function () {},
  webkit: {messageHandlers: {plaudAuthCapture: {postMessage: value => messages.push(value)}}}
};
context.XMLHttpRequest.prototype = {open() {}, setRequestHeader() {}, send() {}};
context.window = context;
vm.runInNewContext(script, context);
for (const url of ['https://api-apne1.plaud.ai/list', 'http://api.plaud.ai/list',
                  'https://api.plaud.ai.evil.invalid/list']) {
  context.fetch(url, {headers: {authorization: 'bearer synthetic', 'x-device-id': 'test'}});
}
context.__plaudNativeCaptureGeneration = 'replaced';
context.fetch('https://api.plaud.ai/list', {headers: {authorization: 'bearer stale'}});
process.stdout.write(JSON.stringify(messages));
"""
    completed = run_local_javascript(
        [NODE, "-e", harness],
        input=shipped_script("plaudAuthCaptureScript"),
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    messages = json.loads(completed.stdout)
    assert len(messages) == 1
    assert messages[0]["kind"] == "capture"
    assert messages[0]["generation"] == "current"
    assert messages[0]["url"] == "https://api-apne1.plaud.ai/list"
