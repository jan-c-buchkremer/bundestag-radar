# bundestag-topic-landscape

A topic landscape of one sitting week of the Bundestag: every speech of the week as a point on a
map, positioned by what it is about, coloured and filterable by fraction, speaker, agenda item and day.

Built on top of [bundestag-data-foundation](https://github.com/jan-c-buchkremer/bundestag-data-foundation),
whose SQLite store is read, never written.

```
speeches of one ISO week  →  multilingual-e5-base embeddings (cached)  →  UMAP + HDBSCAN  →  c-TF-IDF labels  →  one static HTML page
```

## Setup

Python 3.12+, [uv](https://docs.astral.sh/uv/). CPU only; no GPU, no Docker.

```sh
uv sync
export BDF_DB=/path/to/bundestag-data-foundation/data/bundestag.sqlite   # default: ../bundestag-data-foundation/data/bundestag.sqlite
uv run landscape weeks                 # sitting weeks in the store, with sitting numbers and speech counts
uv run landscape build 2026-W28        # → data/out/2026-W28.html
uv run landscape build overview        # period themes (data/out/period.json) + data/out/index.html from the built weeks
uv run landscape build --all           # every week, the period overview and data/out/index.html
uv run landscape serve                 # http://127.0.0.1:8000/ — re-renders pages from data/out/*.json on every request
```

The first build downloads the embedding model (~1.1 GB) into the Hugging Face cache and embeds the week
(about 6 minutes for ~400 speeches on a 2014 quad-core; faster on anything newer). Embeddings are cached in
`data/landscape.sqlite` by model and text hash, so rebuilding a week takes seconds and a model swap is a new
cache key. The whole Wahlperiode (31 weeks) took about three hours on that machine.

Open `data/out/index.html` in a browser, or run `landscape serve` while editing the template (Plotly.js and the
Inter font come from CDNs). Windows: `py -3.12 -m uv …`
works the same.

## Container

CI tests every push; pushes to `main` and `deploy` also publish `ghcr.io/jan-c-buchkremer/bundestag-topic-landscape`
(tags: branch name, short sha). The entrypoint is `landscape`, the working directory `/work`, so the defaults
write to `/work/data/out` and `/work/data/landscape.sqlite`. The foundation store is expected at
`/foundation/bundestag.sqlite` and the model cache at `/cache`:

```sh
docker run --rm -v "$BDF_DATA:/foundation" -v "$PWD/data:/work/data" -v "$PWD/hf-cache:/cache" \
  ghcr.io/jan-c-buchkremer/bundestag-topic-landscape:main build --all
```

Mount the foundation directory writable: the store is in WAL mode and SQLite creates a `-shm` file even for
read-only connections. The store is still opened with `mode=ro`.

## What the pages show

`index.html` opens with the **themes of the Wahlperiode**: one clustering over every speech of the period
(without Regierungsbefragung and Zwischenfragen), drawn as a streamgraph over the sitting weeks. Hover (or a tap) shows theme,
week and count in a tooltip below-right of the pointer that flips to stay on screen; clicking a band picks the theme (its strongest weeks, agenda items and a link into the week), a
second click on the chosen band opens that week with the matching week topic in focus. „Anteil“ switches to the
share of each week. Bands are coloured by policy area (eight hue families plus „Weitere“, assigned from the theme's
top terms); a click on an area in the legend fades the others. Below, the tours and every sitting week with its main topics, newest first. Each week page:

- One point per speech; position from UMAP on the speech embedding; topic colour and label from HDBSCAN + c-TF-IDF.
- Colour by topic, fraction, day, **agenda item** or role. Agenda-item colouring shows where one debate spreads
  across the landscape; each item takes the hue family of its sitting day, light for early items, deep for late ones.
- Filters as collapsible checkbox lists (fraction, day, agenda item, topic, speaker) with live counts: nothing
  checked means everything, OR within a list, AND across lists; ↻ re-sorts a list by frequency in the current view.
  The checkbox filters; a click on the name opens that value's card without filtering.
- Search: typing marks matches on the map, Enter turns the term into a saved filter (several terms are ANDed,
  each can be switched off or removed); matches are highlighted in the opened speech.
- Filtered-out speeches stay on the map at 10 % opacity ("Gefilterte abdunkeln", on by default) and point size
  follows speech length (on by default). „zurücksetzen“ restores all of that, the list order and the zoom.
- The view is kept in the URL, so it can be shared.
- Click a point: the full speech with interjections and applause inline, Zwischenfragen and Kurzinterventionen as
  cards where they were asked, citation and PDF link, the next speech in speaking order and the five most similar
  speeches of the week. Click a topic label or a name in a filter list: bar charts by fraction, day, topic, agenda
  item and speaker, with the speeches listed; every bar opens its own card.
- Regierungsbefragung turns are hidden by default (two-minute question/answer units under one agenda item); an
  explicit speaker or agenda filter shows them anyway. Every Zwischenfrage, whatever its length, is shown in full
  where it was asked in the speech it interrupted, and is also a point of its own that is hidden the same way
  („Zwischenfragen zeigen“, off by default); an explicit speaker filter shows them, and opening one (by link or from
  the speech) switches the toggle on.
- Links out to the [MdB cards](https://jan-c-buchkremer.github.io/bundestag-mdb-cards/): the speaker's name (and
  portrait, hidden when there is none) opens their card, the agenda item its sitting page (`sitzungen/21-88.html#top-6`),
  and every announced result of the agenda item its vote page, with „angenommen“/„abgelehnt“ as a badge.

## Deep links

Everything in the view is in the URL hash of a week page (`<week>.html#…`), so other sites can link into it:

| key | example | effect |
|---|---|---|
| `rede` (alias `open`) | `2026-W28.html#open=ID218816800` | opens that speech. Continuation parts (`ID…-3`) and Kurzinterventionen too short to be points open the speech they belong to; a Zwischenfrage opens as its own point and switches „Zwischenfragen zeigen“ on. |
| `cluster` | `2026-W28.html#cluster=4` | filters to that week topic and opens its card (the overview links this way) |
| `fraction`, `weekday`, `agenda`, `speaker` | `#speaker=Anna Adler` | filters, `\|`-separated values |
| `suche` | `#suche=Miete\|Wohnen` | saved search terms |
| `farbe` | `#farbe=agenda` | colour mode: `cluster`, `fraction`, `weekday`, `agenda`, `mdb` |
| `befragung`, `zwischenfragen`, `dim`, `size`, `showLabels` | `#zwischenfragen=1` | toggles, `1`/`0` |
| `tour` | `#tour=woche` | starts a tour: `woche`, `debatte`, `person` |

### `speech_clusters.json`

Every build writes `data/out/speech_clusters.json` from all week payloads in `data/out`, for sites that link a
speech to its week topic (the MdB cards site shows the topics of an agenda item with it):

```json
{"ID218816800": {"week": "2026-W28", "cluster_id": 4, "label": "Miete · Wohnen · Mietpreisbremse"}, …}
```

Keys are foundation speech ids, including continuation parts (`ID…-3`) and Zwischenfragen; `week` and `cluster_id`
make the link `<week>.html#cluster=<cluster_id>`, `label` is the topic's label as the map shows it (top three terms).
Speeches without a topic (HDBSCAN noise, ceremonial single sittings) are left out. Cluster ids change when a week is
rebuilt, so read the file of the same build as the pages.

### `speech_themes.json` and `speech_neighbours.json`

Also for the cards site, next to `speech_clusters.json` and keyed the same way (not published with the pages):

```json
{"ID218816800": {"theme_id": 3, "label": "Miete · Wohnen · Mietpreisbremse"}, …}
{"ID218816800": ["ID218709300", "ID219102100", …], …}
```

`speech_themes.json` maps each speech to its period theme, with the id and label the index page uses
(`index.html#thema=<theme_id>`); written whenever `period.json` is in the output directory. Speeches outside the
period model (Regierungsbefragung, Zwischenfragen) or without a theme are left out.

`speech_neighbours.json` lists up to five speeches of the whole period whose embeddings are most similar (cosine,
best first), never from the speech's own agenda item; only matches with a similarity of at least 0.88 count, and a
speech without any is left out. Values are the speeches' first foundation ids. Only `build --all` writes it, so a
build of single weeks never replaces it with a partial one.

### `mentions.json`

„Erwähnungen“ for the cards site: which countries, Länder, larger Gemeinden and organisations the speeches name.
`landscape mentions [--out data/out] [--store data/landscape.sqlite] [--jobs N]` (`mentions.py`, separate from the
map builds) runs spaCy's German NER (`de_core_news_lg`, local; installed as a dependency) over every speech in the
store, links the spans it finds to Wikidata QIDs through the gazetteers in `src/landscape/gazetteer/`, and writes

```json
{"model": "de_core_news_lg",
 "entities": {"Q183": {"label": "Deutschland", "kind": "staat"}, …},
 "speeches": {"ID218816800": {"Q183": 3, "Q64": 1}, …}}
```

Speeches are the foundation's speech ids, parts of a rede (`ID…-2`) apart, each with its own counts of mentions
per QID; speeches without a mention are left out. `kind` is `land`, `staat`, `gemeinde` or `organisation`. The NER
result is cached per text hash in the `ner` table of the store (next to the embeddings), so changing a gazetteer
re-links in seconds; the first run over the WP 21 dev data (13 k speeches, 37 M characters) took about 3 minutes with
`--jobs 8`.

The gazetteers are committed. `scripts/fetch_gazetteers.py` refreshes the Wikidata parts (countries, Länder,
Gemeinden of at least 50 000 inhabitants). By hand and to be reviewed: `organisations.tsv` (QID, label, surface
forms), `aliases.tsv` (surface form → QID, also settles ambiguous names such as Frankfurt or Bremen) and
`denylist.txt` (names never linked: Essen, Halle, Hagen …). A Gemeinde only counts where the NER tags a place;
adjectives ("deutsche", "Berliner") are not mentions.

### `topic_origins.json`

„Wer brachte das Thema auf?“ on each theme of the index page, written with every `build overview` / `--all`
(`origin.py`). Per theme its episodes: runs of sitting weeks in which the theme has at least 4 % of the week's
words, after at least 4 sitting weeks below that (shorter dips stay inside the episode). Per episode, kept apart:

- `formal`: the first week's agenda items whose speeches mostly belong to the theme, with the initiators of their
  Drucksachen (via the DIP Vorgang, so a Beschlussempfehlung counts for whoever tabled the Vorlage; the urheber of
  the Drucksache when the Vorgang names none) and, for an Aktuelle Stunde, the fractions that requested it;
- `unprompted`: speeches on the theme in the 8 sitting weeks before, held under agenda items with another main
  theme, per fraction next to the fraction's speeches in those weeks.

Episodes starting in the first 4 sitting weeks are `period_start` and have no `unprompted` part: their lead-up is
not in the data.

The index takes `index.html#thema=<id>` to pre-select a period theme. Theme ids are by size and change when the
period is re-clustered; week topic ids change when a week is rebuilt, so only `rede`/`open` links are stable.

## Touren

Three guided walkthroughs on the real data of a week, started from the **Tour** button in the header or
from the cards on the index page (`<week>.html#tour=woche|debatte|person`). Each step spotlights one
control and waits until you have done the action yourself: *Was war diese Woche los?* (map → topic → speech
→ source), *Eine Debatte über die Landschaft verfolgen* (dim mode, colour by TOP, tick a TOP, colour by
fraction) and *Einer Person folgen* (speaker list, profile, similar speeches by others, sharing the URL).

## How the corpus is cut

- A sitting week is the ISO week of the sitting date. No sitting week in WP 21 crosses a Sunday.
- Speeches split by the foundation at Zwischenfragen (`ID…`, `ID…-2`, …) are re-joined per speaker. Where another
  person spoke in between, the main speech gets a marker paragraph pointing to that person's speech (a
  Kurzintervention if the chair announced one, else a Zwischenfrage), and that speech a marker back.
- Speeches under 500 characters are dropped (procedural remarks, single replies), except Zwischenfragen, which are
  points at any length. A dropped Kurzintervention's text is shipped with the page and opens in place.
- Ministers have no fraction in the protocol; their party from the master data is used instead. Non-MdB
  ministers and Länder representatives are shown as "ohne Fraktion".
- Speeches longer than the model window are embedded in paragraph-boundary chunks and mean-pooled.
- A Zwischenfrage is another person's turn inside a rede that the chair did not announce as a Kurzintervention
  (payload flag `zwischenfrage`). Kurzinterventionen are standalone statements and stay normal points.
- The period overview re-uses the cached week embeddings; its themes are cached in `data/out/period.json` with a
  fingerprint of the speeches and parameters, so a daily `--all` without new sittings only re-counts (~5 s instead of
  ~40 s for UMAP + HDBSCAN on 8.7 k speeches). The whole `build --all` takes about 1.5 minutes with a warm cache.
- The foundation's `decision`, `roll_call_vote.agenda_item_id` and `person_photo` are optional: with an older store
  the vote links are simply missing and every portrait is tried (and hidden if it does not load).

Decisions and their reasons: `docs/decisions.md`. Data examination that preceded the design: `docs/examination.md`.

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```

Tests run against an in-memory copy of the foundation schema; no store, no model download needed.
