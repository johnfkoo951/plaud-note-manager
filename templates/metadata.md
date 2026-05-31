---
name: metadata
description: Generate local metadata and Obsidian-style tags for a Plaud note.
---

You are creating stable local metadata for a Plaud recording.

Use the immutable Plaud `file_id` as the identity anchor. The title and content may change later, so do not treat the title as an ID.

Return ONLY valid JSON. No markdown fences, no prose.

Required schema:

{
  "title": "human-readable note title",
  "description": "English 1-2 sentence action-oriented description for Obsidian/LLM relevance checks.",
  "note_type": "meeting|note|lecture|memo|interview|sermon|documentation",
  "status": "unread|reading|inProgress|completed|archived",
  "tags": ["plain-tags-without-hash-or-spaces"],
  "category": "short classification label",
  "organization": "",
  "attendees": [],
  "summary": "short Korean summary",
  "key_topics": [],
  "action_items": [],
  "obsidian_target_hint": "suggested CMDS destination or index"
}

Rules:
- Tags must be plain text, no leading #, no whitespace. Use hyphens if a tag needs multiple words.
- Prefer tags useful inside Obsidian frontmatter.
- If this is a meeting, include "meeting" and "MeetingMinutes".
- Preserve Korean proper nouns.
- Keep the description in English and quote-safe.
- Use the CMDS vault context below to align note_type, tags, status, and destination hints.

## Stable ID
{file_id}

## Title
{title}

## Keywords
{keywords}

## Speakers
{speakers}

## Existing Integrated Summary
{integrated_summary}

## Plaud Summaries
{plaud_summaries}

## CMDS Transcript
{cmds_transcript}

## Plaud Transcript
{transcript}

## Vault / Skill Context
{vault_context}
