"""The top of the Radar's start page: what a reader who opens plenar-radar.de/radar wants first.

In the order a reader asks (radar-lab research 01): what happened this week (the generated lead „Die Woche im
Bundestag“ over the top stories; index.html puts the hib tiles under it), which stories carried it (the first cards
of `stories.json`, with their "kurz erklärt" and the Fraktionen's positions), and what is coming up („Steigende
Themen“: the rising themes of `themen.json` as one chart, the people of `gespraech.json` as one line of names). Each
part is optional: what is not built is left out, so an older output directory still renders the index as before.
The full lists go to their own pages (`geschichten.html`, `gespraech.html`), linked below each part.

Nothing here selects anything: the order comes from each module's own rule, this module only cuts the lists."""

from __future__ import annotations

import html
import json
from pathlib import Path

from radar import shell

STORIES_ON_START = 6
PEOPLE_ON_START = 6  # besides the news tier, which always shows

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
        f"<title>{html.escape(title)} – Radar</title>{shell.head(research)}<style>{PAGE_CSS}</style>"
        f"{shell.info_assets()}</head>"
        f"<body class='mode-radar'>{shell.site_header(research)}<main class='lab'>"
        f"<span class='eyebrow'>Radar</span>{body}{_more('index.html', 'Zurück zum Radar')}</main></body></html>"
    )


def people_line(payload: dict, n: int = PEOPLE_ON_START) -> str:
    """Im Gespräch in one line under „Steigende Themen“: the names of `short_people`, and the link to the full page
    with every number."""
    people = short_people(payload, n)["people"]
    if not people:
        return ""
    names = ", ".join(html.escape(e["name"]) for e in people)
    return (f'<p class="people"><b>Personen im Gespräch:</b> {names} · <a href="gespraech.html">alle '
            f'{len(payload["people"])} mit Zahlen →</a></p>')  # fmt: skip


def sections(out: Path, research: str) -> dict[str, str]:
    """The parts of index.html's „Diese Woche“ that come from the weekly JSON files, by placeholder: `lead` (Die
    Woche im Bundestag), `stories` (Geschichten der Woche) and `rising` (Steigende Themen, with the people in the
    news in one line); also writes the full pages next to it. A part that is not built is ""."""
    from radar import gespraech, lead, stories

    parts = {"lead": "", "stories": "", "rising": ""}
    st, ld = _load(out, stories.FILE), _load(out, lead.FILE)
    if ld and st and ld.get("week") != st.get("week"):
        ld = None  # a lead of another week would describe the wrong stories
    if ld:
        parts["lead"] = lead.render_section(ld)
    if st:
        cards = lead.with_html(ld) if ld else None  # the "kurz erklärt" and positions as HTML
        parts["stories"] = stories.render_section(st, cards, limit=STORIES_ON_START) + _more(
            "geschichten.html", f"Alle {len(st['stories'])} Geschichten der Woche"
        )
        full = stories.render_section(st, cards, limit=len(st["stories"]))
        (out / "geschichten.html").write_text(page("Geschichten der Woche", full, research), encoding="utf-8")
    people = ""
    if gs := _load(out, gespraech.FILE):
        people = people_line(gs)
        full = gespraech.render_section(gs, research)
        (out / "gespraech.html").write_text(page("Im Gespräch", full, research), encoding="utf-8")
    try:
        from radar import themes
    except ImportError:  # the theme registry is not part of every build
        themes = None
    if themes is not None and (th := _load(out, "themen.json")):
        parts["rising"] = themes.render_rising(th, people)
    elif people:
        parts["rising"] = f'<section class="rise">{people}</section>'
    return parts
