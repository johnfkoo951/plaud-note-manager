# plaud.cmdspace.work

> Public landing page for the **Plaud 5축 가이드** — Web · Desktop · MCP · Skill · App.
> Lives as a `web/` subfolder of the main `plaud-note-manager` repo so that the page, the App code, and the docs travel together.

- **Live**: <https://plaud.cmdspace.work>
- **Deployed**: Vercel project `plaud`
- **DNS**: Cloudflare zone `cmdspace.work` · `CNAME plaud → cname.vercel-dns.com` (proxied=False)
- **Template**: cmdspace-web-builder v4.3 Landing
- **Content SSOT**: [`../docs/PLAUD-ACCESS-LAYERS.md`](../docs/PLAUD-ACCESS-LAYERS.md)
- **Obsidian summary**: `00. Inbox/03. AI Agent/03-1. Claude Code (MBP)/2026-05-20-plaud-access-layers.md`

## Edit & redeploy

```bash
cd ~/DEV/plaud-note-manager/web

# 1) edit index.html  (single-file landing, 1300+ lines)
# 2) (optional) edit OG template + rebuild PNG
bash scripts/build-og.sh

# 3) deploy
vercel deploy --prod --yes
```

## Source of truth

Always edit the **DEV** docs first, then mirror summary to Obsidian:

1. `../docs/PLAUD-ACCESS-LAYERS.md` — 11+ sections, full matrix
2. Obsidian summary note — derived view (≤ 9 sections, decision-focused)
3. `index.html` — public landing (~8 sections: Hero · The Idea · 5 Channels · Capture vs Manage · Read vs Write · Routing · Workflows · CTA; ~1310 lines, narrative + tables)

If the matrices disagree, the DEV doc wins.

## Cross-references

| From | To |
|---|---|
| `web/index.html` | links to live Plaud Web, MCP docs, Desktop page |
| `docs/PLAUD-ACCESS-LAYERS.md` | links to App CLI commands, Swift entry points, Obsidian summary |
| Obsidian summary | links to DEV docs path + live URL |
| `STATUS.md` · root `README.md` | reference `web/` subfolder + live URL |

## Why `web/` and not a separate DEV folder

The page is *about* this App. Co-locating the landing inside `plaud-note-manager/` means:

- One `git status` shows both code and marketing changes
- Cross-links in docs are stable (relative paths)
- The App's CLI commands documented on the page (`plaud rename`, `plaud web`, `plaud classify`) stay in sync with their source
- Future sessions opening the Obsidian summary first will find the DEV repo via the path noted in the frontmatter
