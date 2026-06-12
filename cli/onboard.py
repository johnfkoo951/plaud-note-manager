"""Parse a copied Plaud cURL into our `.env`.

Captures both the auth headers and the `cookie:` line so the embedded
WKWebView can be primed with the same session the cURL came from.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from core.config import write_env_file

REQUIRED_HEADERS = {
    "authorization": "PLAUD_AUTHORIZATION",
    "x-device-id": "PLAUD_X_DEVICE_ID",
    "x-pld-user": "PLAUD_X_PLD_USER",
}

OPTIONAL_HEADERS = {
    "x-pld-tag": "PLAUD_X_PLD_TAG",  # legacy; current web API omits it
    "app-language": "PLAUD_APP_LANGUAGE",
    "app-platform": "PLAUD_APP_PLATFORM",
    "edit-from": "PLAUD_EDIT_FROM",
    "origin": "PLAUD_ORIGIN",
    "referer": "PLAUD_REFERER",
    "timezone": "PLAUD_TIMEZONE",
}

DEFAULTS = {
    "PLAUD_BASE_URL": "https://api-apne1.plaud.ai",
    "PLAUD_APP_LANGUAGE": "en",
    "PLAUD_APP_PLATFORM": "web",
    "PLAUD_EDIT_FROM": "web",
    "PLAUD_ORIGIN": "https://web.plaud.ai",
    "PLAUD_REFERER": "https://web.plaud.ai/",
    "PLAUD_TIMEZONE": "Asia/Seoul",
}


def parse_curl(curl: str) -> dict[str, str]:
    """Return a flat dict suitable for .env writing."""
    out: dict[str, str] = dict(DEFAULTS)

    for raw_line in curl.splitlines():
        line = raw_line.strip().rstrip("\\").strip()
        # Accept -H/-b and the --header/--cookie long flags, quoted or unquoted.
        m = (
            re.match(r"(?:-H|--header|-b|--cookie)\s+'([^']+)'", line)
            or re.match(r'(?:-H|--header|-b|--cookie)\s+"([^"]+)"', line)
            or re.match(r"(?:-H|--header|-b|--cookie)\s+(\S.*)$", line)
        )
        if not m:
            continue
        kv = m.group(1)
        if ":" not in kv:
            continue
        key, _, val = kv.partition(":")
        key = key.strip().lower()
        val = val.strip()

        if key == "cookie":
            out["PLAUD_COOKIE"] = val
            continue

        if key in REQUIRED_HEADERS:
            out[REQUIRED_HEADERS[key]] = val
        elif key in OPTIONAL_HEADERS:
            out[OPTIONAL_HEADERS[key]] = val

    missing = [v for v in REQUIRED_HEADERS.values() if v not in out]
    if missing:
        raise SystemExit(f"missing required headers in cURL: {', '.join(missing)}")
    return out


def write_env(values: dict[str, str], env_path: Path) -> None:
    # No .env backup on purpose: stale copies would keep live credentials on
    # disk forever, and the shared writer locks the file down to 0600.
    write_env_file(values, env_path)
    print(f"wrote {env_path}")


def main(argv: list[str]) -> int:
    env_path = Path(argv[1]) if len(argv) > 1 else Path(".env")
    curl_text = sys.stdin.read()
    if not curl_text.strip():
        print("paste your Plaud cURL on stdin (Ctrl-D to finish)", file=sys.stderr)
        return 1
    values = parse_curl(curl_text)
    write_env(values, env_path)
    has_cookie = "PLAUD_COOKIE" in values
    print(f"cookie captured: {has_cookie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
