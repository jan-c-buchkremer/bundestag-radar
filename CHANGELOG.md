# Changelog

One entry per release (`docs/release.md`). Each starts with one sentence: what can a reader do now that they could
not before? If that sentence is hard to write, the release is not a finished vertical slice yet.

## Unreleased

## v0.3.0 (2026-10-07)

Readers who open the Radar see first what is going on in the Bundestag this week, as „heute im bundestag“ reports
it: by day, Ressort, kind and the Fraktion that tabled it, each item linked to its article, its Vorgang and the
debate in the plenum.

- „Diese Woche“ on top of `index.html` (`woche.py`, `woche.json`); `radar build woche` refreshes it and the index
  without re-clustering. Needs the foundation's `hib_item` and `hib_drucksache` (optional: an older store builds the
  index without the section). The method note names hib's selection and our grouping.
- Fraktion chips: who tabled the Vorgang behind an item (for an Antwort, who asked), from the Vorgang's initiators
  (`woche.einbringer`), in the Fraktion colours.

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
