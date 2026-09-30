"""Mentions of places, countries and organisations in the speeches -> `mentions.json` for the cards site.

spaCy's German NER (`de_core_news_lg`, local) finds candidate spans; a span counts as a mention only if the
gazetteers (Wikidata QIDs, `gazetteer/`) know it. The NER result is cached by (model, text hash) in the landscape
store, so a gazetteer change re-links in seconds without re-running the model.

Precision over recall, since every mention is shown with a link to the speech it came from:
- Gemeinden above `fetch_gazetteers.MIN_POPULATION` only, and only where the NER calls the span a place (LOC).
- `denylist.txt`: names that are also words or names (Essen, Halle, Hagen …) are never linked.
- `aliases.tsv`: reviewed surface forms (USA, China, NRW); the labels come from Wikidata untouched.
- A name in two gazetteers goes to the first of Länder, Staaten, organisations, Gemeinden (Bremen is the Land).
- Nothing is guessed from adjectives ("deutsche", "Berliner"), only the name itself and its genitive."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

MODEL = "de_core_news_lg"
GAZETTEER = Path(__file__).parent / "gazetteer"
PLACE_LABELS = {"LOC"}  # a Gemeinde must be tagged as a place
ANY_LABELS = {"LOC", "ORG", "MISC"}  # countries, Länder and organisations: the NER often says ORG for "Deutschland"
KINDS = {"land": "Bundesland", "staat": "Staat oder Gebiet", "organisation": "Organisation", "gemeinde": "Gemeinde"}
PRIORITY = ("land", "staat", "organisation", "gemeinde")  # who wins a name that two gazetteers share

Span = tuple[str, str]  # (text, NER label)
Tagger = Callable[[list[str]], Iterable[list[Span]]]


def _rows(name: str) -> list[dict]:
    with (GAZETTEER / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def short_forms(label: str) -> list[str]:
    """How a Gemeinde is named without its disambiguating suffix: "Offenbach am Main" -> "Offenbach". Only the
    part before " am|an der|im|in|ob der …" or " (…)"; the caller drops forms that name two places."""
    m = re.match(r"^(.+?)(?: \(.*\)| (?:am|an der|an|im|in|ob der) .+)$", label)
    return [m.group(1)] if m else []


@dataclass
class Gazetteer:
    entities: dict[str, dict]  # qid -> {"label", "kind"}
    surfaces: dict[str, tuple[str, str]]  # surface form -> (qid, kind)

    def link(self, text: str, label: str) -> str | None:
        """The QID of an NER span, or None. Tries the span, then without a genitive "s"."""
        text = " ".join(text.split())
        for form in (text, text[:-1] if text.endswith("s") else ""):
            hit = self.surfaces.get(form) if form else None
            if hit:
                qid, kind = hit
                return qid if label in (PLACE_LABELS if kind == "gemeinde" else ANY_LABELS) else None
        return None


def load_gazetteer() -> Gazetteer:
    deny = {
        line.strip() for line in (GAZETTEER / "denylist.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }  # fmt: skip
    entities: dict[str, dict] = {}
    by_kind: dict[str, dict[str, str]] = {k: {} for k in PRIORITY}  # kind -> surface -> qid
    ambiguous: set[tuple[str, str]] = set()

    def add(kind: str, surface: str, qid: str) -> None:
        if not surface or surface in deny:
            return
        known = by_kind[kind].setdefault(surface, qid)
        if known != qid:
            ambiguous.add((kind, surface))

    for kind, name in (("land", "laender.tsv"), ("staat", "countries.tsv"), ("gemeinde", "gemeinden.tsv")):
        for r in _rows(name):
            entities.setdefault(r["qid"], {"label": r["label"], "kind": kind})
            add(kind, r["label"], r["qid"])
    for r in _rows("gemeinden.tsv"):  # short forms, once every full name is in
        for form in short_forms(r["label"]):
            add("gemeinde", form, r["qid"])
    for r in _rows("organisations.tsv"):
        entities.setdefault(r["qid"], {"label": r["label"], "kind": "organisation"})
        for a in r["aliases"].split("|"):
            add("organisation", a, r["qid"])
    for r in _rows("aliases.tsv"):
        if r["qid"] not in entities:
            raise ValueError(f"aliases.tsv: unknown QID {r['qid']} for {r['surface']!r}")
        kind = entities[r["qid"]]["kind"]  # reviewed: settles a name the gazetteers alone would drop as ambiguous
        by_kind[kind][r["surface"]] = r["qid"]
        ambiguous.discard((kind, r["surface"]))
        if r["surface"] in deny:
            raise ValueError(f"aliases.tsv: {r['surface']!r} is on the denylist")
    surfaces: dict[str, tuple[str, str]] = {}
    for kind in PRIORITY:
        for surface, qid in by_kind[kind].items():
            if (kind, surface) not in ambiguous:
                surfaces.setdefault(surface, (qid, kind))
    return Gazetteer(entities, surfaces)


def open_store(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ner ("
        " model TEXT NOT NULL, text_sha TEXT NOT NULL, spans TEXT NOT NULL, PRIMARY KEY (model, text_sha))"
    )
    return conn


def text_sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def load_tagger(model: str = MODEL, jobs: int = 1) -> Tagger:
    import spacy

    nlp = spacy.load(model, exclude=["parser", "tagger", "morphologizer", "lemmatizer", "attribute_ruler"])
    nlp.max_length = 2_000_000

    def tag(texts: list[str]) -> Iterable[list[Span]]:
        for doc in nlp.pipe(texts, batch_size=8, n_process=jobs):
            yield [(e.text, e.label_) for e in doc.ents]

    return tag


def tag_texts(
    texts: dict[str, str],
    store: sqlite3.Connection,
    model: str = MODEL,
    loader: Callable[[], Tagger] | None = None,
    batch: int = 200,
) -> dict[str, list[Span]]:
    """NER spans per key, from the cache or from the model (loaded only if something is missing)."""
    shas = {k: text_sha(t) for k, t in texts.items()}
    out: dict[str, list[Span]] = {}
    missing: dict[str, list[str]] = {}  # sha -> keys
    for k, sha in shas.items():
        if sha in missing:
            missing[sha].append(k)
            continue
        row = store.execute("SELECT spans FROM ner WHERE model = ? AND text_sha = ?", (model, sha)).fetchone()
        if row:
            out[k] = [tuple(s) for s in json.loads(row[0])]
        else:
            missing[sha] = [k]
    if missing:
        tagger = (loader or (lambda: load_tagger(model)))()
        todo = list(missing.items())
        for i in range(0, len(todo), batch):
            chunk = todo[i : i + batch]
            results = tagger([texts[keys[0]] for _, keys in chunk])
            with store:
                for (sha, keys), spans in zip(chunk, results, strict=True):
                    store.execute("INSERT OR REPLACE INTO ner VALUES (?, ?, ?)", (model, sha, json.dumps(spans)))
                    for k in keys:
                        out[k] = spans
            print(f"  NER {min(i + batch, len(todo))}/{len(todo)} texts")
    return out


def count_mentions(spans: dict[str, list[Span]], gaz: Gazetteer) -> dict[str, dict[str, int]]:
    """{speech id: {qid: mentions}} for the speeches with at least one."""
    out: dict[str, dict[str, int]] = {}
    for sid, ss in spans.items():
        c = Counter(q for text, label in ss if (q := gaz.link(text, label)))
        if c:
            out[sid] = dict(sorted(c.items()))
    return out


def build_payload(speeches: dict[str, dict[str, int]], gaz: Gazetteer, model: str = MODEL) -> dict:
    used = {q for m in speeches.values() for q in m}
    return {
        "model": model,
        "entities": {q: gaz.entities[q] for q in sorted(used)},
        "speeches": dict(sorted(speeches.items())),
    }


def load_speeches(conn: sqlite3.Connection) -> dict[str, str]:
    """Every speech part of the store, {foundation speech id: text}. Parts (`ID…-2`) stay apart: the cards site
    attributes a mention to the part's own speaker and fraction."""
    return {r[0]: r[1] for r in conn.execute("SELECT id, text FROM speech WHERE text IS NOT NULL ORDER BY id")}


def build(conn: sqlite3.Connection, store: sqlite3.Connection, out: Path, jobs: int = 1) -> dict:
    texts = load_speeches(conn)
    spans = tag_texts(texts, store, loader=lambda: load_tagger(MODEL, jobs))
    gaz = load_gazetteer()
    payload = build_payload(count_mentions(spans, gaz), gaz)
    out.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return payload
