"""
tools/build_doc_pages.py — render repo markdown docs as styled HTML pages.

The repo is private, so the site hosts the documentation content itself.
Re-run after editing any source doc:

    .venv/bin/python tools/build_doc_pages.py
"""

import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).parent.parent

PAGES = [
    ("docs/design.md", "design.html", "Design Document"),
    ("docs/memoric-binary.md", "memoric-binary.html", "Memoric Binary RFC"),
    ("ROADMAP.md", "roadmap.html", "Roadmap"),
    ("docs/phase0-validation.md", "validation.html", "Phase 0 Validation"),
    ("docs/phase3-ab-results.md", "phase3-ab-results.html", "Escalation A/B Results"),
    ("docs/enterprise.md", "enterprise.html", "Enterprise Plan"),
]

TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Perdura — {title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,600;0,9..144,700;1,9..144,400&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<script defer src="/_vercel/speed-insights/script.js"></script>
<script defer src="/_vercel/insights/script.js"></script>
<style>
  :root {{ --ink:#0a0e1f; --ink-2:#0e1430; --ink-3:#131a3d; --paper:#f0f3fa;
    --muted:#97a3c4; --faint:#5b6688; --cyan:#2dd9ff; --amber:#ffb454;
    --line:rgba(151,163,196,0.16); }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--ink); color:var(--paper);
    font-family:"IBM Plex Sans",-apple-system,sans-serif; font-size:16.5px;
    line-height:1.7; -webkit-font-smoothing:antialiased; }}
  ::selection {{ background:var(--cyan); color:var(--ink); }}
  nav {{ position:sticky; top:0; z-index:50; background:rgba(10,14,31,0.85);
    backdrop-filter:blur(12px); border-bottom:1px solid var(--line); }}
  .nav-inner {{ max-width:880px; margin:0 auto; padding:0 28px; height:60px;
    display:flex; align-items:center; justify-content:space-between; }}
  .wordmark {{ font-family:"Fraunces",serif; font-weight:700; font-size:1.25rem;
    color:var(--paper); text-decoration:none; }}
  .wordmark .dot {{ color:var(--cyan); }}
  nav .links a {{ color:var(--muted); text-decoration:none; font-size:0.88rem;
    margin-left:20px; }}
  nav .links a:hover {{ color:var(--cyan); }}
  main {{ max-width:880px; margin:0 auto; padding:64px 28px 96px; }}
  .doc-kicker {{ font-family:"IBM Plex Mono",monospace; font-size:0.78rem;
    letter-spacing:0.18em; text-transform:uppercase; color:var(--cyan);
    margin-bottom:10px; }}
  h1,h2,h3,h4 {{ font-family:"Fraunces",Georgia,serif; font-weight:600;
    line-height:1.2; margin:2.2em 0 0.6em; }}
  h1 {{ font-size:2.3rem; margin-top:0; }}
  h2 {{ font-size:1.6rem; border-bottom:1px solid var(--line);
    padding-bottom:0.35em; }}
  h3 {{ font-size:1.25rem; }} h4 {{ font-size:1.05rem; }}
  p, ul, ol {{ margin:0.9em 0; color:var(--muted); }}
  li {{ margin:0.35em 0; }} ul, ol {{ padding-left:1.4em; }}
  strong {{ color:var(--paper); }} em {{ color:var(--paper); }}
  a {{ color:var(--cyan); }}
  blockquote {{ border-left:3px solid var(--amber); padding:0.4em 1.1em;
    margin:1.2em 0; background:var(--ink-2); border-radius:0 8px 8px 0; }}
  code {{ font-family:"IBM Plex Mono",monospace; font-size:0.85em;
    color:var(--cyan); background:var(--ink-3); padding:2px 6px;
    border-radius:4px; }}
  pre {{ background:var(--ink-2); border:1px solid var(--line);
    border-radius:10px; padding:18px 20px; overflow-x:auto; margin:1.2em 0; }}
  pre code {{ background:none; padding:0; color:var(--paper);
    font-size:0.84rem; line-height:1.6; }}
  table {{ border-collapse:collapse; width:100%; margin:1.2em 0;
    font-size:0.92rem; }}
  th, td {{ border:1px solid var(--line); padding:9px 13px; text-align:left;
    color:var(--muted); }}
  th {{ color:var(--paper); background:var(--ink-2);
    font-family:"IBM Plex Mono",monospace; font-size:0.8rem; }}
  hr {{ border:none; border-top:1px solid var(--line); margin:2.4em 0; }}
  footer {{ max-width:880px; margin:0 auto; padding:0 28px 56px;
    font-family:"IBM Plex Mono",monospace; font-size:0.78rem;
    color:var(--faint); }}
</style></head><body>
<nav><div class="nav-inner">
  <a class="wordmark" href="index.html">perdura<span class="dot">.</span></a>
  <div class="links">{navlinks}</div>
</div></nav>
<main><div class="doc-kicker">Documentation</div>
{body}
</main>
<footer>© 2026 Perdura · perdura.network · MIT licensed · open research — content will change</footer>
</body></html>
"""


def build():
    nav = " ".join(
        f'<a href="{out}">{title.split()[0]}</a>' for _, out, title in PAGES
    ) + ' <a href="index.html#access">Request access</a>'
    for src, out, title in PAGES:
        text = (ROOT / src).read_text(encoding="utf-8")
        body = markdown.markdown(text, extensions=["tables", "fenced_code"])
        (ROOT / out).write_text(
            TEMPLATE.format(title=title, body=body, navlinks=nav),
            encoding="utf-8")
        print(f"{src} -> {out}")


if __name__ == "__main__":
    sys.exit(build())
