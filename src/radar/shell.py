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


# The info button: explanations of a chart or section sit behind a small "i" next to its heading instead of grey text
# under it. Hovering (or focusing) shows the text as a tooltip; a click pins it until the next click or Escape. The
# tooltip is `position: fixed`, placed by INFO_JS, so a card's overflow cannot cut it off.
INFO_CSS = """
.info{display:inline-block;vertical-align:.12em;margin-left:6px;line-height:1}
.info-b{width:18px;height:18px;padding:0;border-radius:50%;border:1px solid #d4d4d8;background:#fff;color:#6b7280;
  font:600 11px/16px Georgia,serif;font-style:italic;cursor:pointer;text-align:center}
.info-b:hover,.info.pinned .info-b{border-color:#7c3aed;color:#7c3aed}
.info.pinned .info-b{background:#f1ebff}
.info-t{display:none;position:fixed;z-index:60;width:min(380px,calc(100vw - 24px));padding:10px 12px;border-radius:8px;
  background:#fff;border:1px solid #e4e4e7;box-shadow:0 8px 24px rgba(17,24,39,.14);color:#1f2328;
  font:400 13px/1.5 Inter,system-ui,sans-serif;letter-spacing:0;text-transform:none;text-align:left;white-space:normal;
  max-height:min(60vh,420px);overflow-y:auto;overscroll-behavior:contain}
.info:hover .info-t,.info-b:focus-visible+.info-t,.info.pinned .info-t{display:block}
.gen-tag{display:inline-block;vertical-align:.15em;margin-left:8px;font-size:11px;font-weight:500;color:#5b5670;
  border:1px solid #d9d3ea;border-radius:4px;padding:1px 5px;line-height:1.3}
"""
INFO_JS = """
(() => {
  if (window.__info) return;
  window.__info = true;
  const place = i => {
    const b = i.querySelector('.info-b').getBoundingClientRect(), t = i.querySelector('.info-t');
    const w = Math.min(380, innerWidth - 24);
    t.style.left = Math.max(12, Math.min(b.left - 10, innerWidth - w - 12)) + 'px';
    t.style.top = (b.bottom + 6) + 'px';
    const h = t.getBoundingClientRect().height;
    if (h && b.bottom + 6 + h > innerHeight - 8 && b.top - 6 - h > 8) t.style.top = (b.top - 6 - h) + 'px';
  };
  const close = keep => document.querySelectorAll('.info.pinned').forEach(i => {
    if (i === keep) return;
    i.classList.remove('pinned'); i.querySelector('.info-b').setAttribute('aria-expanded', 'false');
  });
  document.addEventListener('click', e => {
    if (e.target.closest('.info-t')) return;
    const b = e.target.closest('.info-b');
    const i = b && b.parentElement;
    close(i);
    if (!i) return;
    e.preventDefault(); e.stopPropagation();  // a button inside <summary> or a link must not toggle it
    b.setAttribute('aria-expanded', String(i.classList.toggle('pinned')));
    place(i);
  }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(null); });
  for (const ev of ['mouseover', 'focusin']) document.addEventListener(ev, e => {
    const i = e.target.closest && e.target.closest('.info');
    if (i) requestAnimationFrame(() => place(i));
  });
  addEventListener('scroll', () => document.querySelectorAll('.info.pinned').forEach(place), { passive: true });
  addEventListener('resize', () => document.querySelectorAll('.info.pinned').forEach(place));
})();
"""


def info_assets() -> str:
    """The info button's style and script, once per page."""
    return f"<style>{INFO_CSS}</style><script>{INFO_JS}</script>"


def info(text: str, label: str = "Erklärung") -> str:
    """An info button whose tooltip holds `text` (HTML, already escaped by the caller)."""
    return (f'<span class="info"><button type="button" class="info-b" aria-expanded="false" '
            f'aria-label="{html.escape(label)}">i</button>'
            f'<span class="info-t" role="tooltip">{text}</span></span>')  # fmt: skip
