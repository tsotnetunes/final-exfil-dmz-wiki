# DMZ Codex

What is known about the DMZ mode of Call of Duty: Modern Warfare 4 — places, mechanics, items, missions, enemies, patches, known issues, glossary — kept as a Knowledge-as-Code wiki: one Markdown page per subject, with its sources, in plain git. Unofficial; not affiliated with Activision. The same knowledge is shown on finalexfil.com.

**Two writers.** The *Brain* (the Final Exfil app's agents) writes the generated zone of each page from claims that cite evidence it holds, and exports `claims/` and `evidence/`. *People* write the human zone and new pages. Nothing in this repository is X post text: X evidence is kept as post ids and hashes only.

**Reading.** Start at `wiki/hub/overview.md`; `index.md` lists every page with a one-line card; `wiki/hub/open-questions.md` lists what is unknown.

**Contributing.** Edit the human zone of a page (between `<!-- human:start -->` and `<!-- human:end -->`) or add a page with both zones. Keep the card under 200 characters, cite a source page or claim id, and mark anything unverified as such. Never paste X post text or a private person's handle. `python3 tools/kac.py check` before committing. `AGENTS.md` is the full contract, for people and AI agents alike.

**Layout.** `wiki/<type>/<id>.md` pages · `raw/` official documents (immutable) · `claims/`, `evidence/` record stores (Brain only) · `proposals/`, `feedback/` · `evals/golden.yaml` · `tools/kac.py` · `index.md`, `log.md` (generated).
