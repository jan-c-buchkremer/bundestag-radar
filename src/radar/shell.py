"""The site shell on Radar pages (docs/architecture.md: "Radar takes the shell from Research").

Research owns the shell: its published `shell.css` (tokens, the header with its mode, the Radar elements) and
`nav.js` (the search suggestions). Radar pages link both from RESEARCH_URL and write the header markup of Research's
`ui.site_header` with absolute links, in Radar mode: the Radar colour, the Radar tag next to the site's name and the
Radar entry marked as the current section. Radar imports no Research code; when Research's header changes, this
markup follows (the labels and paths are Research's URL scheme, which is a contract)."""

from __future__ import annotations

import html

# Research's top bar, in its order (bundestag-research-platform ui.NAV_LEFT, ui.NAV_RIGHT)
NAV_LEFT = (("abgeordnete.html", "Abgeordnete"), ("orte/index.html", "Orte"), ("gremien/index.html", "Gremien"))
NAV_RIGHT = (
    ("vorgaenge/index.html", "Vorgänge"),
    ("sitzungen/index.html", "Sitzungen"),
    ("regierung/index.html", "Fragen"),
    ("debatte/index.html", "Debattenkultur"),
    ("daten.html", "Daten"),
)
QUIET = {"Debattenkultur"}
RADAR_ICON = (
    '<svg class="ri" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="6.5" '
    'fill="none" stroke="currentColor" stroke-width="1.3" opacity=".45"/><circle cx="8" cy="8" r="3.5" fill="none" '
    'stroke="currentColor" stroke-width="1.3" opacity=".7"/><path d="M8 8 12.6 3.4" stroke="currentColor" '
    'stroke-width="1.6" stroke-linecap="round"/><circle cx="8" cy="8" r="1.4" fill="currentColor"/></svg>'
)
LOGO = (
    '<svg class="logo" viewBox="0 0 28 16" width="28" height="16" aria-hidden="true">'
    '<path d="M3 15a11 11 0 0 1 11-11" fill="none" stroke="var(--research)" stroke-width="2.6" stroke-linecap="round" '
    'stroke-dasharray="0 4.6"/><path d="M14 4a11 11 0 0 1 11 11" fill="none" stroke="var(--radar)" '
    'stroke-width="2.6" stroke-linecap="round" stroke-dasharray="0 4.6"/>'
    '<path d="M8 15a6 6 0 0 1 12 0" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" '
    'stroke-dasharray="0 4.4" opacity=".55"/></svg>'
)


def brand(research: str, cls: str = "") -> str:
    """The site's name with the Radar tag, leading to the front page."""
    r = html.escape(research)
    classes = f"brand {cls}".strip()
    return (f'<a href="{r}index.html" class="{classes}" aria-label="plenar-radar.de, Startseite">{LOGO}'
            f'<span class="wm">plenar<b>radar</b></span><span class="mode">{RADAR_ICON}Radar</span></a>')  # fmt: skip


def head(research: str) -> str:
    """The shell's stylesheet, before a page's own styles."""
    return f'<link rel="stylesheet" href="{html.escape(research)}shell.css">'


def site_header(research: str, landscape: str = "index.html") -> str:
    """Research's top bar on a Radar page: every link absolute, the Radar entry current."""
    r = html.escape(research)

    def link(href: str, label: str) -> str:
        quiet = ' class="quiet"' if label in QUIET else ""
        return f'<a href="{r}{href}"{quiet}>{label}</a>'

    search = (
        f'<form class="nav-q" role="search" action="{r}suche.html" data-root="{r}">'
        '<button type="button" class="nav-qi" aria-label="Suche öffnen" aria-expanded="false">'
        '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="none" '
        'stroke="currentColor" stroke-width="1.6"/><path d="M11 11l3.5 3.5" stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round"/></svg></button>'
        '<input type="search" name="q" placeholder="Suche" autocomplete="off" '
        'aria-label="Suche: Person, Ort, Vorgang …">'
        '<div class="nav-sug" hidden></div></form>'
    )
    left = "".join(link(*x) for x in NAV_LEFT)
    right = "".join(link(*x) for x in NAV_RIGHT)
    radar = f'<a href="{html.escape(landscape)}" class="to-radar on" aria-current="page">{RADAR_ICON}Radar</a>'
    return (
        f'<header class="m-radar"><nav class="site" aria-label="Bereiche"><div class="nav-l">{search}{left}</div>'
        f'<div class="nav-c">{brand(research, "home")}</div><div class="nav-r">{right}{radar}</div></nav>'
        f'<script src="{r}nav.js" defer></script></header>'
    )


def method_note(text: str) -> str:
    """The footer note of every Radar page: how its results were made and what they can and cannot tell."""
    return (f'<aside class="radar-note"><span class="rmark">{RADAR_ICON}So entsteht diese Seite</span>'
            f"<p>{text}</p></aside>")  # fmt: skip


# The Themenlandschaft's method (docs/architecture.md gives it as the example)
METHOD = (
    "Jede Rede als Embedding (multilingual-e5-base), angeordnet mit UMAP, gruppiert mit HDBSCAN und benannt nach "
    "typischen Wörtern (c-TF-IDF). Themen zeigen Ähnlichkeit der Wortwahl. Über Positionen sagen sie nichts, und "
    "sie können sich mit neuen "
    "Sitzungswochen ändern. Jede Rede führt zu ihrer Sitzung und ihrem Protokoll in der Recherche."
)
