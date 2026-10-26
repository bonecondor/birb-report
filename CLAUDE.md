# birb-report

Static site for birb.report — a personal site and blog (the plainer, longer writing; not foid, fringe, or intervals). GitHub Pages serves `docs/` on `main`; nothing is live until it lands on `main`. A GitHub Action rebuilds `docs/` on every push that touches `posts/`, `pages/`, `build.py`, or `style.css`.

## How to publish a post (the ONLY workflow — do not improvise)

1. Save the text VERBATIM to `posts/YYYY-MM-DD.md` (second post same day: `posts/YYYY-MM-DD-two.md`). Never rewrite, fix, or restyle the prose. Only add links the user explicitly asks for.
2. Optional frontmatter — `title:` and `dek:` (one-line subtitle). Add them only if the user gives them. A first line `# Title` also works.
3. `python3 build.py` → confirm "built N post(s)".
4. Commit the post and `docs/` together, message `post: YYYY-MM-DD`, push to `main`. No PRs.

## Rules
- Never delete a post. "Undo" = move it to `drafts/` and rebuild.
- Publish times are automatic (first git commit of the file, New York time). `time:` frontmatter overrides.
- Entries are numbered chronologically (no. 001 is the oldest). Untitled posts show as "Entry 00N".
- `pages/*.md` become top-level pages (`about.html`, `elsewhere.html`).
- `python3 build.py --drafts` writes a local preview to `preview/` including `drafts/`. Never commit `preview/`.
- Email: `BUTTONDOWN` in build.py holds the Buttondown username. Buttondown's RSS-to-email reads `https://birb.report/feed.xml`, so a post landing on `main` also emails subscribers. Don't rename post files after publishing (it changes the URL and can resend).
