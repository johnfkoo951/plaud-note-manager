"""Frontmatter for notes landing in the LLM Wiki vault as raw sources.

The wiki vault is not the mothership with different folders — it is a separate
contract, and its own schema says not to mix the two:

- `type` comes from the wiki vocabulary (`raw-source`), not `transcript`
  or `meeting`, which do not exist there.
- `author` records the actual writer and the ingesting agent. The mothership's
  fixed `"[[구요한]]"` owner policy is explicitly *not* applied here.
- `model` / `effort` are required for agent-written pages, not optional.
- A raw source additionally requires `source`, `date ingested`, `category`, and
  `collectionPurpose` — eleven required fields against the mothership's seven.

A Plaud recording is a natural fit: `category: Transcripts` is already in the
wiki's enum, and the immutable-source role matches a transcript, which is
evidence rather than compiled knowledge.

Canonical rules: `CMDS_LLM_Wiki/CLAUDE.md` and the shared profile bundle at
`90. Settings/94. Agent Settings/schema/wiki-profile.json` (`wiki-raw-source`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import app_config
from .frontmatter import (
    advanced_uri_link,
    clean_tags,
    iso_minute,
    model_label,
    wikilink,
    yaml_quote,
)

# wiki-raw-source `category` enum.
CATEGORIES = ("Articles", "Papers", "Books", "Transcripts", "Clippings", "AI Research")

# wiki-raw-source `status` enum. Standard Ingest writes `ingested`.
STATUSES = ("ingested", "stub", "reading", "completed")

#: The body heading that must carry the untouched source text.
ORIGINAL_CONTENT_HEADING = "## Original Content"


class WikiSchemaError(ValueError):
    """The note would not satisfy the wiki-raw-source contract."""


@dataclass
class WikiRawSourceFrontmatter:
    """A `type: raw-source` page for the LLM Wiki vault.

    `description` and `collection_purpose` have no default because the schema
    will not accept the note without them, and `collection_purpose` in
    particular is the user's own statement of why this was collected — it is
    not something to infer.
    """

    description: str
    collection_purpose: str
    source: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # Human contributors, emitted as quoted wikilinks.
    authors: list[str] = field(default_factory=list)
    # The ingesting agent, emitted as a plain string beside them.
    agent: str = ""
    model: str = ""
    effort: str = ""
    date_created: str = ""
    date_modified: str = ""
    date_ingested: str = ""
    category: str = "Transcripts"
    status: str = "ingested"
    # Cross-vault: the mothership this came from, and what it links back to.
    source_vault: str = ""
    main_vault_related: list[dict[str, str]] = field(default_factory=list)  # title / rel_path
    main_vault_cmds: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Raise when a required field is missing or an enum value is wrong.

        Called before writing so a malformed note never reaches the vault,
        where it would sit as a silent schema violation.
        """
        if not self.description.strip():
            raise WikiSchemaError("description is required (English, 1-2 sentences)")
        if not self.collection_purpose.strip():
            raise WikiSchemaError(
                "collectionPurpose is required — state why this was collected "
                "and how it will be reused"
            )
        if not self.source.strip():
            raise WikiSchemaError("source is required (the original URL or reference)")
        if self.category not in CATEGORIES:
            raise WikiSchemaError(f"category must be one of: {', '.join(CATEGORIES)}")
        if self.status not in STATUSES:
            raise WikiSchemaError(f"status must be one of: {', '.join(STATUSES)}")
        if not (self.authors or self.agent):
            raise WikiSchemaError("author is required (contributors and/or the ingesting agent)")

    def lines(self) -> list[str]:
        self.validate()
        out = ["---"]
        out.append("type: raw-source")

        out.append("aliases:" if self.aliases else "aliases: []")
        out.extend(f"  - {yaml_quote(a)}" for a in self.aliases)

        out.append("description: " + yaml_quote(self.description))

        # Humans as wikilinks, the agent as a plain scalar — the wiki records
        # who wrote it, not who owns the vault.
        out.append("author:")
        out.extend(f"  - {wikilink(a)}" for a in self.authors)
        if self.agent:
            out.append(f"  - {self.agent}")

        # Required for agent-written wiki pages, and ordered right after author.
        out.append(f"model: {yaml_quote(self.model or 'unknown')}")
        out.append(f"effort: {yaml_quote(self.effort or app_config.model_effort())}")

        created = self.date_created or iso_minute()
        out.append(f"date created: {created}")
        out.append(f"date modified: {self.date_modified or created}")
        out.append(f"date ingested: {self.date_ingested or created}")

        tags = clean_tags(self.tags)
        out.append("tags:" if tags else "tags: []")
        out.extend(f"  - {t}" for t in tags)

        out.append(f"source: {yaml_quote(self.source)}")
        out.append(f"category: {self.category}")
        out.append("collectionPurpose: " + yaml_quote(self.collection_purpose))
        out.append(f"status: {self.status}")

        if self.source_vault:
            out.append(f"source-vault: {self.source_vault}")
        if self.main_vault_cmds:
            out.append(f"mainVaultCmds: {wikilink(self.main_vault_cmds)}")
        if self.source_vault and self.main_vault_related:
            out.append("mainVaultRelated:")
            for rel in self.main_vault_related:
                title = rel.get("title") or ""
                path = rel.get("rel_path") or ""
                if not title or not path:
                    continue
                link = advanced_uri_link(f"Main: {title}", self.source_vault, path)
                out.append(f"  - {yaml_quote(link)}")

        for key, value in self.extra.items():
            out.append(f"{key}: {value}")

        out.append("---")
        return out

    def render(self) -> str:
        return "\n".join(self.lines())


def build_raw_source_note(
    *,
    frontmatter: WikiRawSourceFrontmatter,
    title: str,
    original_content: str,
    summary: str = "",
    plaud_url: str = "",
    recorded_date: str = "",
) -> str:
    """Assemble a complete wiki raw-source note.

    The schema is explicit that a raw source must distinguish the original from
    any summary of it, so the verbatim text goes under `## Original Content`
    and anything generated sits above it, clearly separated.
    """
    if not original_content.strip():
        raise WikiSchemaError(
            "a raw source with no original content is not a source — "
            "run the transcription first"
        )

    out = frontmatter.lines()
    out.append("")
    out.append(f"# {title}".rstrip())
    out.append("")

    callout = "> [!info] Plaud recording"
    if recorded_date:
        callout += f" · {recorded_date}"
    out.append(callout)
    if plaud_url:
        out.append(f"> [web.plaud.ai]({plaud_url})")
    out.append("")

    if summary.strip():
        body = summary.strip()
        # Templates emit their own section headings. Adding "## Summary" above
        # one would nest a heading under a heading for no reason.
        if not body.startswith("#"):
            out.append("## Summary")
            out.append("")
        out.append(body)
        out.append("")
        out.append(
            "> [!note] The section above is generated. "
            "The source of record is Original Content below."
        )
        out.append("")

    out.append(ORIGINAL_CONTENT_HEADING)
    out.append("")
    out.append(original_content.strip())
    out.append("")
    return "\n".join(out)


def agent_label(model: str) -> str:
    """The `author` entry for the ingesting agent.

    The wiki names the agent (`Claude`, `Codex`), while `model` carries the
    exact identifier.
    """
    return {
        "claude": "Claude",
        "codex": "Codex",
        "gemini": "Gemini",
        "grok": "Grok",
    }.get(model, model or "unknown")


def model_fields(model: str) -> tuple[str, str]:
    """`(model, effort)` for the frontmatter, from the configured provider."""
    return model_label(model), app_config.model_effort()
