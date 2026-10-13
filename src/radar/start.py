"""The top of the Radar's start page: what a reader who opens plenar-radar.de/radar wants first.

In the order a reader asks (radar-lab research 01): what happened this week (the generated lead „Die Woche im
Bundestag“ over the top stories), which stories carried it (the first cards of `stories.json`, with their "kurz
erklärt" and the Fraktionen's positions), who and what is talked about (`gespraech.json`, shortened to the news
and a few names), and which themes rose (`themen.json`). Each part is optional: what is not built is left out, so
an older output directory still renders the index as before. The full lists go to their own pages
(`geschichten.html`, `gespraech.html`, `themen.html`), linked below each part.

Nothing here selects anything: the order comes from each module's own rule, this module only cuts the lists."""

from __future__ import annotations

import html
import json
from pathlib import Path

from radar import shell

STORIES_ON_START = 6
PEOPLE_ON_START = 6  # besides the news tier, which always shows
THEMES_ON_START = 4

PAGE_CSS = """
.start-more{margin:6px 0 28px;font-size:.9rem}
.start-more a{color:var(--radar,#7c3aed);font-weight:600}
main.lab{max-width:1100px;margin:0 auto;padding:12px 16px 40px}
"""


def _load(out: Path, name: str) -> dict | None:
    path = out / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _more(href: str, label: str) -> str:
    return f'<p class="start-more"><a href="{html.escape(href)}">{html.escape(label)} →</a></p>'


def short_people(payload: dict, n: int = PEOPLE_ON_START) -> dict:
    """Im Gespräch for the start page: everyone in the news tier, then the first `n` of the other groups in their
    own order (groups, then names: no rank)."""
    news = [e for e in payload["people"] if e["group"] == "nachricht"]
    rest = [e for e in payload["people"] if e["group"] != "nachricht"][:n]
    return {**payload, "people": news + rest}


def page(title: str, body: str, research: str) -> str:
    """A full Radar page in the site's shell for one of the long lists."""
    return (
        "<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)} – Radar</title>{shell.head(research)}<style>{PAGE_CSS}</style></head>"
        f"<body class='mode-radar'>{shell.site_header(research)}<main class='lab'>"
        f"<span class='eyebrow'>Radar</span>{body}{_more('index.html', 'Zurück zum Radar')}</main></body></html>"
    )


def sections(out: Path, research: str) -> str:
    """The HTML for the top of index.html; also writes the full pages next to it."""
    from radar import gespraech, lead, stories

    parts = []
    st, ld = _load(out, stories.FILE), _load(out, lead.FILE)
    if ld and st and ld.get("week") != st.get("week"):
        ld = None  # a lead of another week would describe the wrong stories
    if ld:
        parts.append(lead.render_section(ld))
    if st:
        parts.append(stories.render_section(st, ld, limit=STORIES_ON_START))
        parts.append(_more("geschichten.html", f"Alle {len(st['stories'])} Geschichten der Woche"))
        full = stories.render_section(st, ld, limit=len(st["stories"]))
        (out / "geschichten.html").write_text(page("Geschichten der Woche", full, research), encoding="utf-8")
    if gs := _load(out, gespraech.FILE):
        parts.append(gespraech.render_section(short_people(gs), research))
        parts.append(_more("gespraech.html", f"Alle {len(gs['people'])} Personen und die Themen im Gespräch"))
        full = gespraech.render_section(gs, research)
        (out / "gespraech.html").write_text(page("Im Gespräch", full, research), encoding="utf-8")
    try:
        from radar import themes
    except ImportError:  # the theme registry is not part of every build
        themes = None
    if themes is not None and (th := _load(out, "themen.json")):
        parts.append(themes.render_section(th, limit=THEMES_ON_START))
        parts.append(_more("themen.html", "Alle Themen der Woche"))
        full = themes.render_section(th, limit=None)
        (out / "themen.html").write_text(page("Themen der Woche", full, research), encoding="utf-8")
    return "".join(parts)
