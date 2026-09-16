"""Single builder for CMDS-standard frontmatter on every note this app writes.

Mirrors the vault's `.claude/rules/frontmatter-standard.md`:

- 7 required fields: type · aliases · description · author · date created ·
  date modified · tags
- `description` English, always double-quoted; wikilinks in YAML quoted;
  ISO 8601 `YYYY-MM-DDTHH:mm`; hyphen arrays; no numeric-only tags.
- Agent-written notes carry `model:` + `effort:` right after `author:` so the
  vault's lane classification can tell human from machine without folder
  heuristics.
- `CMDS:` → 📚 second-level subcategory only; `index:` → 🏷 index note only.

Plaud lane extras (matching the existing 08-1. Plaud notes): `date`
(recording date, distinct from ingest time), `source: plaud`, `plaud_id`,
`dual`, `speakers`/`attendees` as quoted wikilinks, `keywords`, `related`
(resolved vault notes), `reuse-channels`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

from . import app_config
from .tags import normalize_tags

ISO_MINUTE = "%Y-%m-%dT%H:%M"


def yaml_quote(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def wikilink(target: str) -> str:
    """Quoted `"[[target]]"` — YAML-safe wikilink for frontmatter arrays."""
    clean = str(target).strip()
    if clean.startswith("[[") and clean.endswith("]]"):
        clean = clean[2:-2]
    return yaml_quote(f"[[{clean}]]")


def iso_minute(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime(ISO_MINUTE)


def advanced_uri_link(label: str, vault_name: str, rel_path: str) -> str:
    """Cross-vault link per the vault's wikilink-rules §6: `[[…]]` cannot cross
    vault boundaries, so use an Advanced URI markdown link instead."""
    path = rel_path if rel_path.endswith(".md") else f"{rel_path}.md"
    return f"[{label}](obsidian://advanced-uri?vault={quote(vault_name)}&filepath={quote(path)})"


def clean_tags(tags: list[str]) -> list[str]:
    """Normalize and drop numeric-only tags (they break Obsidian Properties)."""
    out = []
    for tag in normalize_tags(tags):
        if tag and not tag.replace("-", "").replace("_", "").isdigit():
            out.append(tag)
    return out


def model_label(model: str) -> str:
    """Best-effort model id for `model:` — the configured API id, else the
    provider name (CLI subscriptions pick their own default model)."""
    return app_config.model_id_for(model) or model


@dataclass
class CmdsFrontmatter:
    type: str
    description: str
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    author: str = ""
    model: str = ""
    effort: str = ""
    date_created: str = ""
    date_modified: str = ""
    date: str = ""  # recording date
    cmds: str = ""  # "📚 840 Lectures" (no brackets)
    index: str = ""  # "🏷 Meeting Notes"
    status: str = "inProgress"
    source: str = "plaud"
    plaud_id: str = ""
    dual: bool | None = None
    speakers: list[str] = field(default_factory=list)
    attendees: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    reuse_channels: list[str] = field(default_factory=list)
    # Cross-vault: when the note lands in the wiki vault, main-vault notes are
    # referenced via `mainVaultRelated:` advanced-uri links, never `[[…]]`.
    source_vault: str = ""
    main_vault_related: list[dict[str, str]] = field(default_factory=list)  # title/rel_path
    extra: dict[str, str] = field(default_factory=dict)  # plain scalar extras

    def lines(self) -> list[str]:
        out = ["---"]
        out.append(f"type: {self.type}")
        out.append("aliases:" if self.aliases else "aliases: []")
        out.extend(f"  - {yaml_quote(a)}" for a in self.aliases)
        out.append("description: " + yaml_quote(self.description))
        if self.author:
            out.append("author:")
            out.append(f"  - {wikilink(self.author)}")
        if self.model:
            out.append(f"model: {yaml_quote(self.model)}")
            out.append(f"effort: {yaml_quote(self.effort or app_config.model_effort())}")
        created = self.date_created or iso_minute()
        out.append(f"date created: {created}")
        out.append(f"date modified: {self.date_modified or created}")
        if self.date:
            out.append(f"date: {self.date}")
        tags = clean_tags(self.tags)
        out.append("tags:" if tags else "tags: []")
        out.extend(f"  - {t}" for t in tags)
        if self.cmds:
            out.append(f"CMDS: {wikilink(self.cmds)}")
        if self.index:
            out.append(f"index: {wikilink(self.index)}")
        out.append(f"status: {self.status}")
        out.append(f"source: {self.source}")
        if self.plaud_id:
            out.append(f"plaud_id: {self.plaud_id}")
            out.append(f"plaud_url: https://web.plaud.ai/file/{self.plaud_id}")
        if self.dual is not None:
            out.append(f"dual: {'true' if self.dual else 'false'}")
        if self.speakers:
            out.append("speakers:")
            out.extend(f"  - {wikilink(s)}" for s in self.speakers)
        if self.attendees:
            out.append("attendees:")
            out.extend(f"  - {wikilink(s)}" for s in self.attendees)
        if self.keywords:
            out.append("keywords:")
            out.extend(f"  - {yaml_quote(k)}" for k in self.keywords)
        if self.related:
            out.append("related:")
            out.extend(f"  - {wikilink(r)}" for r in self.related)
        if self.source_vault:
            out.append(f"source-vault: {self.source_vault}")
        if self.main_vault_related and self.source_vault:
            out.append("mainVaultRelated:")
            for note in self.main_vault_related:
                label = f"Main: {note.get('title', '')}"
                out.append(
                    "  - "
                    + yaml_quote(
                        advanced_uri_link(label, self.source_vault, note.get("rel_path", ""))
                    )
                )
        if self.reuse_channels:
            # `channel:status` pairs so Obsidian Bases can filter reuse material.
            out.append("reuse-channels:")
            out.extend(f"  - {c}" for c in self.reuse_channels)
        for key, value in self.extra.items():
            out.append(f"{key}: {value}")
        out.append("---")
        return out

    def render(self) -> str:
        return "\n".join(self.lines())
