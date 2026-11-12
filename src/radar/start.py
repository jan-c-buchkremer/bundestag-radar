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

STORIES_ON_START = 12
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


def de(iso: str) -> str:
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


# hib's kinds in the plural, for the pills of „Diese Woche“
PLURAL = {"Kleine Anfrage": "Kleine Anfragen", "Antwort": "Antworten", "Anhörung": "Anhörungen", "Antrag": "Anträge",
          "Unterrichtung": "Unterrichtungen", "Gesetzentwurf": "Gesetzentwürfe", "Bericht": "Berichte",
          "Beschlussempfehlung": "Beschlussempfehlungen", "Verordnung": "Verordnungen"}  # fmt: skip


def _tag(n: int, one: str, many: str) -> str:
    return f'<span class="utag">{n} {html.escape(one if n == 1 else many)}</span>'


def hib_tags(wk: dict) -> list[str]:
    """One pill per hib kind of the week, the most frequent first: „4 Anhörungen“."""
    kinds: dict[str, int] = {}
    for i in wk.get("items", []):
        k = i.get("kind") or "Meldung"
        kinds[k] = kinds.get(k, 0) + 1
    return [_tag(n, k, PLURAL.get(k, k)) for k, n in sorted(kinds.items(), key=lambda kv: -kv[1])]


def plenum_tags(out: Path, st: dict) -> list[str]:
    """The pills of a sitting week: its Themenlandschaft (a link) first, then sitting days, speeches, votes."""
    week = st["week"]
    tags = [f'<a class="utag" href="{week}.html">Themenlandschaft</a>'] if (out / f"{week}.html").exists() else []
    tags.append(_tag(len(st["sittings"]), "Sitzungstag", "Sitzungstage"))
    if wp := _load(out, f"{week}.json"):
        tags.append(_tag(len(wp["speeches"]), "Rede", "Reden"))
        if n := sum(len(v) for v in wp.get("decisions", {}).values()):
            tags.append(_tag(n, "Abstimmung", "Abstimmungen"))
    return tags


def tags(items: list[str]) -> str:
    return f'<span class="utags">{"".join(items)}</span>' if items else ""


def sitting_part(st: dict, out: Path | None = None) -> str:
    """The heading of the newest sitting week when it is not the running week: its lead and stories follow."""
    first, last = st["sittings"][0]["date"], st["sittings"][-1]["date"]
    days = de(first) if first == last else f"{de(first)} bis {de(last)}"
    kw = int(st["week"].split("-W")[1])
    pills = tags(plenum_tags(out, st)) if out else ""
    return (f'<div class="part part-now part-sit" id="sitzungswoche"><span class="part-k">Letzte Sitzungswoche</span>'
            f'<span class="part-d">KW {kw} · {days}</span>{pills}</div>')  # fmt: skip


PARTS = ("now", "lead", "stories", "rising", "sitting", "jump", "nowtags", "sitrising")  # index.html's __START_*__


def sections(out: Path, research: str) -> dict[str, str]:
    """The parts of index.html's „Diese Woche“ that come from the weekly JSON files, by placeholder: `lead` (Die
    Woche im Bundestag), `stories` (Beraten und entschieden) and `rising` (Steigende Themen, with the people in the
    news in one line); also writes the full pages next to it. A part that is not built is ""."""
    from radar import gespraech, lead, stories

    parts = dict.fromkeys(PARTS, "")
    st, ld = _load(out, stories.FILE), _load(out, lead.FILE)
    wk = _load(out, "woche.json")
    if st and st.get("sittings") and (not wk or wk.get("week") != st["week"]):
        parts["sitting"] = sitting_part(st, out)
        parts["jump"] = '<a class="jump" href="#sitzungswoche">Letzte Sitzungswoche ↓</a>'
        now = _load(out, lead.NOW_FILE)
        if now and wk and now.get("week") == wk.get("week"):
            parts["now"] = lead.render_section(now, "Bisher in dieser Woche")
    if wk:  # in a sitting week „Diese Woche“ holds the plenum too, and its pills say so
        same = st and st.get("sittings") and wk.get("week") == st["week"]
        parts["nowtags"] = tags((plenum_tags(out, st) if same else []) + hib_tags(wk))
    if ld and st and ld.get("week") != st.get("week"):
        ld = None  # a lead of another week would describe the wrong stories
    if ld:
        parts["lead"] = lead.render_section(ld)
    if st:
        cards = lead.with_html(ld) if ld else None  # the "kurz erklärt" and positions as HTML
        n = len(stories.shown(st))
        rest = n - STORIES_ON_START
        more = _more("geschichten.html", f"{rest} weitere") if rest > 0 else ""
        parts["stories"] = stories.render_section(st, cards, limit=STORIES_ON_START) + more
        full = stories.render_section(st, cards, limit=n)
        (out / "geschichten.html").write_text(page(stories.TITLE, full, research), encoding="utf-8")
    people = ""
    if gs := _load(out, gespraech.FILE):
        people = people_line(gs)
        full = gespraech.render_section(gs, research)
        (out / "gespraech.html").write_text(page("Im Gespräch", full, research), encoding="utf-8")
    try:
        from radar import themes
    except ImportError:  # the theme registry is not part of every build
        themes = None
    th = _load(out, "themen.json") if themes is not None else None
    if th and any(t["rising_in"] for t in th["themes"]):  # a quiet week shows no empty chart, only the people
        parts["rising"] = themes.render_rising(th, people)
    elif people:
        parts["rising"] = f'<section class="rise">{people}</section>'
    ts = _load(out, themes.SITTING_FILE) if parts["sitting"] and themes is not None else None
    if ts and st and ts.get("week") == st["week"]:
        parts["sitrising"] = themes.render_rising(ts)
    return parts
