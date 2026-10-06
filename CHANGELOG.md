# Changelog

One entry per release (`docs/release.md`). Each starts with one sentence: what can a reader do now that they could
not before? If that sentence is hard to write, the release is not a finished vertical slice yet.

## Unreleased

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
