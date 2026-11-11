# Changelog

One entry per release (`docs/release.md`). Each starts with one sentence: what can a reader do now that they could
not before? If that sentence is hard to write, the release is not a finished vertical slice yet.

## Unreleased

Readers who open the Radar see what is going on in the Bundestag now: what „heute im bundestag“ reported this week by
policy area, with a short generated summary and the themes that are rising, then the newest sitting week as a
checked lead and its Geschichten der Woche, and who is in the news.

- Two week units (docs/decisions.md, 2026-11-11). „Diese Woche“ is the running calendar week, rebuilt every night:
  the hib mosaic (`woche.py`, `woche.json`; areas sized by items, Fraktion chips for who tabled the Vorgang, no tile
  narrower than 100 px), „Bisher in dieser Woche“ and „Steigende Themen“. The newest sitting week follows under its
  own heading with its sitting days when it is not the running week.
- „Die Woche im Bundestag“ (`lead.py`, `llm.py`, `lead.json`) and „Bisher in dieser Woche“ (`lead-woche.json`):
  generated in Einfacher Sprache from the sources of the top stories only; every sentence names its sources, and
  code checks that each number and name is in them; failing sentences get one retry, then are dropped. Cached by a
  hash of the inputs in `data/llm-cache/`.
- Geschichten der Woche (`stories.py`, `stories.json`, `geschichten.html`): the sitting week's Vorgänge ranked by
  how many sources carry them (hib, Drucksachen, agenda, votes), with their stage, a „kurz erklärt“ and one sentence
  per Fraktion from the debate.
- „Steigende Themen“ (`themes.py`, `themen.json`): about 60 themes refitted every Monday on the 26 weeks before
  (speeches and hib items), every new document sorted in each night. A theme keeps its id and name while it holds
  the same documents; a retired one names its successor. Kleine Anfragen and Antworten are sorted in and counted,
  but never shape a theme or make it rise. Theme names are generated once per new theme.
- Im Gespräch (`gespraech.py`, `gespraech.html`): members whose Wikipedia readers, plenary activity or hib
  mentions rise well above their own 90 days before, over the last seven days, and the Sachgebiete, Aktuelle
  Stunden and Regierungsbefragung of the week.
- Links to a hib Meldung use the short form `bundestag.de/<id>`, as the Parlamentsnachrichten asked; the text of a
  Meldung is never shown.
- New nightly steps besides `build --all`: `radar themes weekly` (refit when due, name new themes), `radar build
  themen`, `radar build lead` (needs a logged-in `claude` CLI or `--backend api`), `radar build gespraech`, then
  `radar build woche` for the index.
- The Wahlperiode stream places its labels again once the web font has loaded, so none sticks out on a phone.

## v0.2.2 (2026-10-16)

The Themenlandschaft counts its readers like the rest of the site, without cookies (Research's Datenschutz page).

- The pages report their view to Research's reader count (GoatCounter on the same server, no cookies; see
  Research's Datenschutz page).

## v0.2.1 (2026-10-06)

Readers see a speech's tooltip on the map directly above the speech, not wherever the mouse happens to be.

- The tooltip takes the point's position from the map's axes; near the edges its arrow moves along (#17).

## v0.2.0 (2026-10-06)

Readers can tell at a glance that the Themenlandschaft is the Radar: it has the site's header, the Radar colour and a
note on how its topics were made, and it links back to every part of the research platform.

- `shell.py`: Research's header in Radar mode, and Research's published `shell.css` and `nav.js`. Release Research
  first, because a Radar page without the published `shell.css` shows an unstyled header.
- Violet as the accent. Links into Research (cards, sittings, votes) stay blue (`a.ev`).
- The method note sits under the filters of a week's map and above the footer of the overview.
- A speech given in a government office counts for the Bundesregierung, as on the research platform, with the
  speaker's fraction kept beside it; Zwischenfragen, Kurzinterventionen and the Befragung come from the
  foundation's speech parts and agenda kinds instead of the map's own rules (#13). Needs a store from foundation
  v0.1.0 or later.

## v0.1.0 (2026-10-04)

Readers can see what the Bundestag debated in each sitting week as a map of topics, and who spoke on which, down to
every speech.

The first tag: the Themenlandschaft as it is live on 2026-10-04.
