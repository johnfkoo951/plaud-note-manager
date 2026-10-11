"""Load and manage prompt templates from `templates/`.

A template is a markdown file with optional YAML-ish frontmatter:

    ---
    name: meeting
    description: Meeting minutes summary
    kind: summary            # summary | integrated | internal; missing → summary
    deprecated: true         # optional; true | yes | 1
    replaced_by: note-meeting  # optional; the template to use instead
    ---

    <prompt body with {placeholders}>

`kind` says which pipeline a template belongs to: `summary` runs through
`summarize()` with {title}, {keywords}, {speakers}, {transcript};
`integrated` runs through `integrate()` with the CMDS/Plaud inputs; `internal`
templates are loaded by name by their own code (classify, metadata, …).

The app's TemplateMeta reader parses the same files with the same rules, so
keep the two identical: front matter is the text between a leading `---\\n`
and the first `\\n---\\n`; only `key: value` lines starting at column 0 count;
the first occurrence of a key wins; a value loses one pair of matching
surrounding quotes, or else a trailing ` #comment`. `kind` is lowercased with
the aliases `summarize` → summary and `integrate` → integrated, and anything
unknown loads as `summary` — loading never fails on a bad kind.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import TEMPLATES_DIR, template_path

KIND_SUMMARY = "summary"
KIND_INTEGRATED = "integrated"
KIND_INTERNAL = "internal"
KINDS = (KIND_SUMMARY, KIND_INTEGRATED, KIND_INTERNAL)
KIND_ALIASES = {"summarize": KIND_SUMMARY, "integrate": KIND_INTEGRATED}

_OPEN = "---\n"
_CLOSE = "\n---\n"


class TemplateKindError(ValueError):
    """A template was handed to a pipeline of a different kind."""


def _is_blank(ch: str) -> bool:
    # Swift's CharacterSet.whitespaces: Unicode Zs plus tab.
    return ch == "\t" or unicodedata.category(ch) == "Zs"


def _trim(text: str) -> str:
    start, end = 0, len(text)
    while start < end and _is_blank(text[start]):
        start += 1
    while end > start and _is_blank(text[end - 1]):
        end -= 1
    return text[start:end]


def _clean_value(raw: str) -> str:
    """One pair of matching quotes, or else a trailing ` #comment`, removed.
    A `#` inside quotes, or not preceded by whitespace (`C#`), is kept."""
    value = _trim(raw)
    if value[:1] in ('"', "'"):
        end = value.find(value[0], 1)
        if end != -1:
            tail = _trim(value[end + 1 :])
            if not tail or tail.startswith("#"):
                return value[1:end]
    if value.startswith("#"):
        return ""
    cuts = [i for i in (value.find(" #"), value.find("\t#")) if i != -1]
    if cuts:
        value = value[: min(cuts)]
    return _trim(value)


def _line_key(line: str) -> str | None:
    """The key of a column-0 `key: value` line; None for anything else."""
    if not line or line[0] in " \t#" or ":" not in line:
        return None
    return _trim(line.split(":", 1)[0]) or None


def parse_frontmatter(raw: str) -> tuple[list[str], dict[str, str], str]:
    """(front matter lines, first-occurrence key → cleaned value, body).

    Newlines are normalised first (`\\r\\n` and a lone `\\r` become `\\n`),
    as Python's universal newlines and the app's reader both do. Without a
    front matter block the lines and keys are empty and the whole text is the
    body; otherwise the body is everything after the closing `---` line.
    """
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if raw.startswith(_OPEN):
        close = raw.find(_CLOSE, len(_OPEN))
        if close != -1:
            lines = raw[len(_OPEN) : close].split("\n")
            meta: dict[str, str] = {}
            for line in lines:
                key = _line_key(line)
                if key and key not in meta:
                    meta[key] = _clean_value(line.split(":", 1)[1])
            return lines, meta, raw[close + len(_CLOSE) :]
    return [], {}, raw


def _canonical_kind(value: str) -> str | None:
    value = value.lower()
    value = KIND_ALIASES.get(value, value)
    return value if value in KINDS else None


def normalize_kind(raw: str | None) -> str:
    """The kind a template is read as: missing or unknown → summary.

    `raw` is the already-cleaned front matter value and is matched as it is,
    with no second trim — the app does the same, so `" integrated "` (quoted
    padding) is unknown on both sides rather than integrated on one.
    """
    return _canonical_kind(raw or "") or KIND_SUMMARY


def validate_kind(raw: str) -> str:
    """Strict form for writes: an unknown kind is an error, not a summary.
    Typed input is trimmed first; the canonical value is what gets written."""
    kind = _canonical_kind(_trim(raw))
    if kind is None:
        raise ValueError(
            f"unknown template kind {raw!r} — use one of: {', '.join(KINDS)} "
            f"(aliases: {', '.join(KIND_ALIASES)})"
        )
    return kind


@dataclass
class Template:
    name: str
    description: str
    body: str
    path: Path
    kind: str = KIND_SUMMARY
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def deprecated(self) -> bool:
        return self.meta.get("deprecated", "").lower() in {"true", "yes", "1"}

    @property
    def replaced_by(self) -> str:
        return self.meta.get("replaced_by", "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "deprecated": self.deprecated,
            "replaced_by": self.replaced_by,
            "path": str(self.path),
        }

    def render(
        self,
        *,
        transcript: str = "",
        title: str = "",
        keywords: str = "",
        speakers: str = "",
        cmds_transcript: str = "",
        plaud_transcript: str = "",
        plaud_summaries: str = "",
        **extra: str,
    ) -> str:
        values = {
            "transcript": transcript,
            "title": title,
            "keywords": keywords,
            "speakers": speakers,
            "cmds_transcript": cmds_transcript,
            "plaud_transcript": plaud_transcript,
            "plaud_summaries": plaud_summaries,
            **{key: str(value) for key, value in extra.items()},
        }
        out = self.body
        for key, value in values.items():
            out = out.replace("{" + key + "}", value)
        return out


def require_kind(template: Template, expected: str) -> None:
    """Refuse a template meant for another pipeline, saying what to run instead."""
    if template.kind == expected:
        return
    name = template.name
    if template.kind == KIND_INTERNAL:
        detail = "is internal: it belongs to its own pipeline; pick one from `plaud templates`"
    elif expected == KIND_SUMMARY:
        detail = (
            "is an integrated template — run "
            f"`uv run plaud cmds-integrate <file_id> --template {name}`"
        )
    elif expected == KIND_INTEGRATED:
        detail = (
            "is a summary template — run "
            f"`uv run plaud cmds-summarize <file_id> --template {name}`, "
            f"or add `kind: integrated` to the front matter of templates/{name}.md"
        )
    else:
        detail = f"is kind {template.kind!r}, not {expected!r}"
    raise TemplateKindError(f"template '{name}' {detail}")


def load_template(name: str) -> Template:
    path = template_path(name)
    if not path.exists():
        raise FileNotFoundError(f"template not found: {name} ({path})")
    _lines, meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    return Template(
        name=name,
        description=meta.get("description", ""),
        body=body,
        path=path,
        kind=normalize_kind(meta.get("kind")),
        meta=meta,
    )


def list_templates() -> list[Template]:
    if not TEMPLATES_DIR.exists():
        return []
    out: list[Template] = []
    for p in sorted(TEMPLATES_DIR.glob("*.md")):
        try:
            out.append(load_template(p.stem))
        except Exception:
            continue
    return out


def _field_line(key: str, value: str) -> str:
    """`key: value`, quoted when the reader would otherwise cut or unquote it."""
    value = re.sub(r"\r\n|[\r\n]", " ", value)
    if _clean_value(value) != value:
        for quote in ('"', "'"):
            if quote not in value:
                value = f"{quote}{value}{quote}"
                break
    return f"{key}: {value}" if value else f"{key}:"


def _set_field(lines: list[str], key: str, line: str) -> bool:
    """Replace the effective (first) `key:` line in place; False when absent."""
    for i, existing in enumerate(lines):
        if _line_key(existing) == key:
            lines[i] = line
            return True
    return False


def save_template(
    name: str, body: str, *, description: str | None = None, kind: str | None = None
) -> Path:
    """Write `body` under the existing front matter, changing only what is given.

    `name:` is set in place; `description:` and `kind:` only when passed;
    every other line keeps its position. Exactly one blank line separates the
    block from the body, so load → save → load is byte-stable.
    """
    kind = validate_kind(kind) if kind is not None else None  # before any write
    path = template_path(name)
    lines: list[str] = []
    if path.exists():
        lines, _meta, _old_body = parse_frontmatter(path.read_text(encoding="utf-8"))
    if not lines:  # new file, or one without a front matter block
        lines = [_field_line("name", name), _field_line("description", description or "")]
    else:
        if not _set_field(lines, "name", _field_line("name", name)):
            lines.insert(0, _field_line("name", name))
        if description is not None:
            line = _field_line("description", description)
            if not _set_field(lines, "description", line):
                at = next((i + 1 for i, ln in enumerate(lines) if _line_key(ln) == "name"), 0)
                lines.insert(at, line)
    if kind is not None and not _set_field(lines, "kind", _field_line("kind", kind)):
        lines.append(_field_line("kind", kind))

    path.parent.mkdir(parents=True, exist_ok=True)
    text = _OPEN + "\n".join(lines) + "\n---\n\n" + body.lstrip("\n")
    path.write_text(text, encoding="utf-8")
    return path


def delete_template(name: str) -> None:
    path = template_path(name)
    if path.exists():
        path.unlink()
