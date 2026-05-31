---
name: vault-meeting-note
description: Write a CMDS main-vault meeting note from Plaud/CMDS transcripts and optional user draft notes.
---

You are writing a finished Korean meeting note for the user's main Obsidian CMDS vault.

Use the vault/system context, Claude Code meeting-minutes skill, CMDS doc formatting skill, and YAML frontmatter conventions below. The user may have written manual thoughts while recording; treat draft context as high-signal intent and integrate it with the transcript.

Return ONLY the final markdown note. Do not wrap it in a code fence.

Required output:
- Full YAML frontmatter at top.
- `type: meeting`.
- Include `description` in English, double-quoted, 1-2 sentences.
- Include an `author` frontmatter field only if one is provided in the vault context; otherwise omit it.
- Include `date created`, `date modified`, and `date` as ISO dates.
- Include `index: "[[🏷 Meeting Notes]]"`, `status: inProgress`, `source: plaud`, and `plaud_id: {file_id}`.
- Frontmatter tags are plain text, no leading #, no spaces.
- Body should use: info callout, `## Summary`, `## Discussion`, `## Decisions`, `## Next Steps`, `## Speaker Map`, optional `## Raw Quotes`, and `## Transcript`.
- Use concise Korean business style with `~임`, `~함`, or noun-form endings where natural.
- Preserve important dates, names, organizations, decisions, numbers, and action items.
- Use the CMDS transcript for speaker labels when available, and Plaud transcript/summaries for coverage.

## Plaud ID
{file_id}

## Meeting Date
{meeting_date}

## Title
{title}

## Keywords
{keywords}

## Speakers
{speakers}

## User Draft / Manual Notes
{draft_context}

## Integrated Summary
{integrated_summary}

## Plaud Summaries
{plaud_summaries}

## CMDS Transcript
{cmds_transcript}

## Plaud Transcript
{transcript}

## Vault / Skill Context
{vault_context}
