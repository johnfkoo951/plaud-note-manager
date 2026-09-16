"""Lint (and optionally fix) frontmatter of Plaud-lane notes in the vault.

Checks the notes this app has landed (`source: plaud`) against the vault's
frontmatter-standard: ISO `T` dates, `date modified`, `aliases`, quoted
`description`, `model`/`effort` for agent-written notes, no numeric-only
tags, quoted wikilinks in YAML. Fixes are line-local — key order and body are
never touched — and every rewrite is reported so the user can diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import app_config

DEFAULT_LANE = "00. Inbox/08. Transcripts/08-1. Plaud"
_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_SPACE_DATE = re.compile(r"^(date created|date modified):\s*(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})\s*$")
_BARE_WIKILINK = re.compile(r"^(\s*-\s+)(\[\[[^\]]+\]\])\s*$")


@dataclass
class LintResult:
    path: Path
    issues: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _split(text: str) -> tuple[str, str] | None:
    m = _FM_RE.match(text)
    if not m:
        return None
    return m.group(1), text[m.end() :]


def lint_text(
    text: str, *, fix: bool = False, model_label: str = ""
) -> tuple[str, list[str], list[str]]:
    """Return (new_text, issues, fixed). With fix=False new_text == text."""
    parts = _split(text)
    if parts is None:
        return text, ["no frontmatter"], []
    fm, body = parts
    lines = fm.split("\n")
    issues: list[str] = []
    fixed: list[str] = []
    keys = {
        ln.split(":", 1)[0].strip() for ln in lines if ln and not ln.startswith(" ") and ":" in ln
    }

    out: list[str] = []
    for ln in lines:
        m = _SPACE_DATE.match(ln)
        if m:
            issues.append(f"{m.group(1)}: space-separated time (use T)")
            if fix:
                ln = f"{m.group(1)}: {m.group(2)}T{m.group(3)}"
                fixed.append(f"{m.group(1)} → ISO T")
        if ln.startswith("description:"):
            value = ln.split(":", 1)[1].strip()
            if value and not (value.startswith('"') and value.endswith('"')):
                issues.append("description not double-quoted")
                if fix:
                    ln = "description: " + '"' + value.replace('"', '\\"') + '"'
                    fixed.append("description quoted")
        wl = _BARE_WIKILINK.match(ln)
        if wl:
            issues.append(f"bare wikilink in YAML: {wl.group(2)}")
            if fix:
                ln = f'{wl.group(1)}"{wl.group(2)}"'
                fixed.append("wikilink quoted")
        if re.match(r"^\s*-\s+\d+\s*$", ln):
            issues.append(f"numeric-only tag/list item: {ln.strip()}")
            if fix:
                fixed.append(f"dropped {ln.strip()}")
                continue
        out.append(ln)

    def _after(key: str, new_line: str) -> None:
        for i, ln in enumerate(out):
            if ln.startswith(key + ":"):
                # skip the key's own block (indented continuation lines)
                j = i + 1
                while j < len(out) and out[j].startswith(" "):
                    j += 1
                out.insert(j, new_line)
                return
        out.append(new_line)

    if "date modified" not in keys:
        issues.append("missing date modified")
        if fix:
            created = next(
                (ln.split(":", 1)[1].strip() for ln in out if ln.startswith("date created:")), ""
            )
            _after("date created", f"date modified: {created}")
            fixed.append("date modified added")
    if "aliases" not in keys:
        issues.append("missing aliases")
        if fix:
            _after("type", "aliases: []")
            fixed.append("aliases: [] added")
    if "model" not in keys:
        issues.append("missing model/effort (agent-written note)")
        if fix and model_label:
            _after("author", f'model: "{model_label}"')
            _after("model", f'effort: "{app_config.model_effort()}"')
            fixed.append("model/effort added")

    new_fm = "\n".join(out)
    new_text = f"---\n{new_fm}\n---\n{body}" if fix and fixed else text
    return new_text, issues, fixed


def lint_lane(
    vault: Path | None = None,
    *,
    lane: str = DEFAULT_LANE,
    fix: bool = False,
    model_label: str = "",
) -> list[LintResult]:
    vault = vault or app_config.obsidian_vault()
    if vault is None:
        return []
    folder = vault / lane
    if not folder.is_dir():
        return []
    results: list[LintResult] = []
    for path in sorted(folder.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "source: plaud" not in text.split("\n---", 2)[0] if text.startswith("---") else True:
            continue
        new_text, issues, fixed = lint_text(text, fix=fix, model_label=model_label)
        if fix and fixed and new_text != text:
            path.write_text(new_text, encoding="utf-8")
        results.append(LintResult(path=path, issues=issues, fixed=fixed))
    return results
