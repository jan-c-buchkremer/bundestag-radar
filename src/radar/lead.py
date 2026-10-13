"""„Die Woche im Bundestag“: generated texts on top of the stories, each sentence tied to its sources and checked.

Three kinds of text, all in Einfache Sprache (B1, after DIN 8581-1; not "Leichte Sprache", which needs a check by
a test group), all from the sources of `stories.json` and nothing else:

- the lead: 120–200 words over the top stories (`stories.TOP_N`), from their hib texts, Drucksache titles,
  decisions and debates;
- per top story a "kurz erklärt" of 2–3 sentences: what it is about, who wants what, what happened this week;
- per top story with a debate one sentence per Fraktion from its speeches ("Positionen in der Debatte").

The selection is the stories' ranking, never the model's. The model gets every source as a document with an id
("hib 787/2026", "Drucksache 21/8379", "Abstimmung 21/99/5", "Debatte 21/99/2", "Rede ID219901600") and must end
every sentence with the ids it rests on. Code then checks each sentence (`check`): it has an id, every id is one of
the inputs, every number and every name of a Fraktion, person, ministry, Bundesregierung or Bundesrat in it
occurs in the sources it cites, and a position sentence cites a speech of its own Fraktion. If any sentence fails,
the model gets one retry with the failures named; sentences that fail again are dropped. Every check is logged in
lead.json, so the page can say what was verified and a reviewer can see what was dropped.

Outputs are cached by a hash of backend, model, prompt and sources in data/llm-cache/: a rerun on the same week
costs nothing, a changed source regenerates only the texts that read it."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from radar import llm, stories
from radar.llm import Document

FILE = "lead.json"
LABEL = (
    "Automatisch erstellt aus den Meldungen von „heute im bundestag“ und den Daten des Bundestages. Jeder Satz "
    "nennt seine Quellen. Der Text kann Fehler enthalten."
)
LEAD_WORDS = (120, 200)
MAX_SENTENCE_WORDS = 20  # Einfache Sprache: a longer sentence is logged as a warning, not dropped
MAX_REDEN_PER_FRACTION = 3  # speeches given to the model per Fraktion, the longest first
MAX_REDE_CHARS = 6000
FRACTION_LABEL = {
    "CDU/CSU": "CDU/CSU",
    "SPD": "SPD",
    "AfD": "AfD",
    "BÜNDNIS 90/DIE GRÜNEN": "Grüne",
    "Die Linke": "Linke",
}
MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

SYSTEM = """Du schreibst für plenar-radar.de kurze Texte über die Arbeit des Deutschen Bundestages. Du arbeitest wie \
eine neutrale Nachrichtenredaktion.

Regeln für den Inhalt:
- Verwende nur Fakten aus den mitgelieferten Quellen. Ergänze nichts aus deinem Wissen: keine Namen, Zahlen, Daten \
oder Hintergründe, die nicht in den Quellen stehen.
- Bewerte nicht. Keine wertenden Wörter wie „wichtig“, „umstritten“, „heftig“, „endlich“, „Streit“. Keine \
Vermutungen über Folgen oder Chancen.
- Sag, wer was tut: welche Fraktion, welches Ministerium, welcher Ausschuss etwas beantragt, fragt, beschließt oder \
ablehnt.
- Behandle alle Fraktionen gleich. Nenne die Haltung einer Fraktion nur, wenn eine Quelle sie nennt.
- Schreibe „die Grünen“ für BÜNDNIS 90/DIE GRÜNEN, „die Linke“ für Die Linke, „CDU/CSU“ für die Unionsfraktion.

Regeln für die Sprache (Einfache Sprache, Niveau B1):
- Kurze Sätze mit höchstens 20 Wörtern. Ein Gedanke pro Satz.
- Aktiv statt Passiv. Alltagswörter statt Fachwörter.
- Erkläre ein Fachwort beim ersten Mal kurz, zum Beispiel: „Ein Antrag ist ein Vorschlag an den Bundestag.“
- Keine Ketten aus Genitiven wie „die Änderung des Gesetzes zur Regelung der …“. Keine Abkürzungen außer \
Parteinamen.
- Zahlen als Ziffern.

Regeln für die Quellen:
- Jeder Satz endet mit den Kennungen seiner Quellen in eckigen Klammern, genau so geschrieben wie im Attribut id \
der Quelle, zum Beispiel [hib 787/2026] oder [hib 787/2026; Drucksache 21/8379]. Das gilt auch für Sätze, die ein \
Wort erklären: Sie nennen die Quelle, in der das Wort vorkommt.
- Nenne nur Quellen, in denen die Aussage des Satzes steht. Jede Zahl und jeder Name im Satz muss in einer \
genannten Quelle stehen.
- Schreibe nur den Text: keine Überschrift, keine Einleitung, keine Aufzählungszeichen, kein Markdown. Ein Satz \
pro Zeile."""

TASK_LEAD = """Schreibe den Text „Die Woche im Bundestag“ für die Woche vom {start} bis {end}. Er handelt von diesen \
Geschichten, in dieser Reihenfolge:
{stories}

Länge: {lo} bis {hi} Wörter, ohne die Klammern gezählt. Der erste Satz nennt drei oder vier Themen der Woche. \
Danach folgen zwei oder drei kurze Absätze. Trenne Absätze mit einer Leerzeile."""

TASK_KURZ = """Erkläre die Geschichte „{title}“ in 2 bis 3 Sätzen. Sag, worum es geht, wer was will und was in der \
Woche vom {start} bis {end} passiert ist."""

TASK_POSITIONS = """Die Quellen enthalten Reden aus der Debatte im Bundestag zu „{title}“. Schreibe für jede dieser \
Fraktionen genau einen neutralen Satz darüber, was ihre Rednerinnen und Redner in der Debatte fordern, ablehnen \
oder begründen: {fractions}.
Beginne jede Zeile mit dem Namen der Fraktion, genau wie in der Liste, und einem Doppelpunkt, zum Beispiel \
„SPD: Die SPD will …“. Nenne in der Klammer die Rede, aus der die Aussage stammt. Gib wieder, was gesagt wurde; \
schreibe „fordert“, „lehnt ab“, „sagt“, nicht deine Einschätzung."""

RETRY = """Einige Sätze deines letzten Textes haben die Prüfung nicht bestanden:
{failures}
Schreibe den ganzen Text noch einmal nach denselben Regeln. Lass jede Aussage weg, die nicht in den genannten \
Quellen steht."""


@dataclass
class Source:
    """A document for the model plus what the page needs to link it."""

    id: str
    text: str
    url: str
    label: str
    fraction: str | None = None  # a speech: the Fraktion it counts for

    def document(self) -> Document:
        return Document(self.id, self.text)


# ------------------------------------------------------------------------------------------------------- sources


def german_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day}. {MONTHS[d.month - 1]} {d.year}"


def _hib_sources(conn: sqlite3.Connection, items: list[dict]) -> list[Source]:
    """hib items with their full text (allowed for generated summaries with a link to each item, Jan 2026-10-12)."""
    if not items:
        return []
    texts = {
        r[0]: r[1]
        for r in conn.execute(
            f"SELECT id, text FROM hib_item WHERE id IN ({','.join('?' * len(items))})", [i["id"] for i in items]
        )
    }
    return [
        Source(
            f"hib {i['number']}",
            f"heute im bundestag {i['number']}, {german_date(i['date'])}. Ressort: "
            f"{i['ressort']}. Art: {i['kind']}.\nÜberschrift: {i['title']}\n{texts.get(i['id'], '')}",
            i["url"],
            f"hib {i['number']}",
        )
        for i in items
    ]


def _drucksache_sources(conn: sqlite3.Connection, ds: list[dict]) -> list[Source]:
    out = []
    for d in ds:
        row = conn.execute(
            "SELECT originators FROM drucksache WHERE number = ? AND coalesce(publisher, 'BT') = 'BT'", (d["number"],)
        ).fetchone()
        who = ", ".join(json.loads(row[0])) if row and row[0] else ""
        date = f", {german_date(d['date'])}" if d.get("date") else ""
        text = f"Drucksache {d['number']} ({d.get('type') or 'Drucksache'}{date}).\nTitel: {d.get('title') or ''}"
        out.append(
            Source(
                f"Drucksache {d['number']}",
                text + (f"\nUrheber: {who}" if who else ""),
                d["pdf"],
                f"Drucksache {d['number']}",
            )
        )
    return out


POSITION_WORD = {"yes": "dafür", "no": "dagegen", "abstain": "Enthaltung"}


def _vote_sources(votes: list[dict]) -> list[Source]:
    out = []
    for v in votes:
        if v.get("shared"):
            continue
        kind = "namentliche Abstimmung" if v["kind"] == "namentlich" else "Abstimmung per Handzeichen"
        lines = [
            f"Abstimmung im Bundestag am {german_date(v['date'])} ({kind}).",
            f"Gegenstand: {v.get('title') or v['subject']}",
            f"Ergebnis: {v.get('result') or 'nicht bekannt'}",
        ]
        if "yes" in v:
            lines.append(f"Stimmen: {v['yes']} Ja, {v['no']} Nein, {v['abstain']} Enthaltungen.")
        if v.get("fraktionen"):
            lines.append(
                "Fraktionen: " + ", ".join(f"{f} {POSITION_WORD.get(p, p)}" for f, p in v["fraktionen"].items())
            )
        out.append(Source(f"Abstimmung {v['id']}", "\n".join(lines), v["url"], "Abstimmung"))
    return out


def _debate_sources(conn: sqlite3.Connection, plenum: list[dict]) -> list[Source]:
    out = []
    for p in plenum:
        if not p["reden"]:
            continue
        row = conn.execute("SELECT title FROM agenda_item WHERE id = ?", (p["agenda_id"],)).fetchone()
        title = (row[0] if row and row[0] else p["title"]).replace(" | ", "; ")
        counts = ", ".join(f"{f} {n}" for f, n in p["fraktionen"].items())
        out.append(
            Source(
                f"Debatte {p['agenda_id']}",
                f"Debatte im Bundestag am {german_date(p['date'])}, "
                f"{p['top']}: {title}\n{p['reden']} Reden. Reden nach Fraktion: {counts}.",
                p["url"],
                f"Debatte {german_date(p['date'])}",
            )
        )
    return out


def _rede_sources(conn: sqlite3.Connection, reden: list[dict]) -> list[Source]:
    """The longest MAX_REDEN_PER_FRACTION speeches of each Fraktion (not the government's), full text up to
    MAX_REDE_CHARS."""
    by_fraction: dict[str, list[tuple[int, dict, str]]] = {}
    for r in reden:
        if r["fraction"] not in FRACTION_LABEL:
            continue
        text = "\n".join(
            t
            for (t,) in conn.execute(
                "SELECT text FROM speech WHERE rede_id = ? AND interruption IS NULL ORDER BY position", (r["id"],)
            )
        )
        by_fraction.setdefault(r["fraction"], []).append((len(text), r, text))
    out = []
    for f, rs in by_fraction.items():
        for _, r, text in sorted(rs, key=lambda x: -x[0])[:MAX_REDEN_PER_FRACTION]:
            out.append(
                Source(
                    f"Rede {r['id']}",
                    f"Rede von {r['speaker']} ({f}).\n{text[:MAX_REDE_CHARS]}",
                    r["url"],
                    f"Rede {r['speaker']}",
                    fraction=f,
                )
            )
    return out


def story_sources(conn: sqlite3.Connection, story: dict, max_drucksachen: int = 4) -> list[Source]:
    """What the model may read about one story (no speeches: those are for the positions)."""
    ds = story["drucksachen"]
    linked = {n for h in story["hib"] for n in h.get("drucksachen", [])}
    ds = [d for d in ds if d["number"] in linked] + [d for d in ds if d["number"] not in linked]
    return (
        _hib_sources(conn, story["hib"])
        + _drucksache_sources(conn, ds[:max_drucksachen])
        + _vote_sources(story["abstimmungen"][:6])
        + _debate_sources(conn, story["plenum"])
    )


def unique(sources: list[Source]) -> list[Source]:
    """One source per id; two hib items of the same issue get "/1", "/2" (the page links each, labelled alike)."""
    seen: dict[str, Source] = {}
    for s in sources:
        if s.id in seen and seen[s.id].url == s.url:
            continue
        seen[s.id if s.id not in seen else f"{s.id}/{sum(k.startswith(s.id) for k in seen) + 1}"] = s
    out = []
    for k, s in seen.items():
        s.id = k
        out.append(s)
    for s in out:  # the first of a shared issue gets its number too, so the model sees they differ
        if any(o.id.startswith(f"{s.id}/") for o in out):
            s.id = f"{s.id}/1"
    return out


# ------------------------------------------------------------------------------------------------- verification

_CITES = re.compile(r"\[([^\[\]]+)\]")
_SENTENCE = re.compile(r"(.*?[.!?…“\"])\s*((?:\[[^\[\]]+\]\s*)+)")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
NUMBER_WORDS = {
    1: "ein",
    2: "zwei",
    3: "drei",
    4: "vier",
    5: "fünf",
    6: "sechs",
    7: "sieben",
    8: "acht",
    9: "neun",
    10: "zehn",
    11: "elf",
    12: "zwölf",
}
# names that must occur in a cited source when a sentence names them: a pattern for the sentence and the words
# any of which a source must contain (lower case)
NAMES = {
    "CDU/CSU": (
        re.compile(r"\b(CDU|CSU|Unionsfraktion)\b|(?<!Europäische )(?<!Europäischen )\bUnion\b"),
        ("cdu", "csu", "union"),
    ),
    "SPD": (re.compile(r"\bSPD\b"), ("spd",)),
    "AfD": (re.compile(r"\bAfD\b"), ("afd",)),
    "Grüne": (re.compile(r"\bGrünen?\b|BÜNDNIS 90"), ("grüne", "bündnis 90")),
    "Linke": (re.compile(r"\bLinken?\b"), ("linke",)),
    "Bundesregierung": (re.compile(r"\b(Bundes)?[Rr]egierung\b"), ("regierung",)),
    "Bundesrat": (re.compile(r"\bBundesrat(es|s)?\b"), ("bundesrat", "länder")),
    "Ministerium": (re.compile(r"[Mm]inisteri|[Mm]inister(in)?\b"), ("minister",)),
}
_ROLE_NAME = re.compile(
    r"(?i:minister(?:in)?|sekretär(?:in)?|beauftragte[rn]?|abgeordnete[rn]?|kanzler(?:in)?|"
    r"präsident(?:in)?)\s+(?:Dr\.\s+)?([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)?)"
)


def strip_cites(text: str) -> str:
    return re.sub(r"\s*\[[^\[\]]+\]", "", text).strip()


def parse(text: str) -> list[dict]:
    """The model's text as sentences: {text (without ids), cites, paragraph}. A sentence ends with its bracketed
    ids; text after the last id of a line is a sentence without sources (kept, so the check can fail it)."""
    out, paragraph = [], 0
    for line in text.strip().splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if not line:
            paragraph += 1 if out and out[-1]["paragraph"] == paragraph else 0
            continue
        pos = 0
        for m in _SENTENCE.finditer(line):
            cites = [
                c.strip()
                for g in _CITES.findall(m.group(0))
                for c in re.split(
                    r";|,(?=\s*(?:hib|Drucksache|"
                    r"Abstimmung|Debatte|Rede)\b)",
                    g,
                )
            ]
            out.append({"text": strip_cites(m.group(1)), "cites": [c for c in cites if c], "paragraph": paragraph})
            pos = m.end()
        if rest := line[pos:].strip():
            out.append({"text": strip_cites(rest), "cites": [], "paragraph": paragraph})
    return out


def words(text: str) -> int:
    return len(strip_cites(text).split())


def _norm_numbers(text: str) -> str:
    """1.234 -> 1234 (thousands), keep decimal commas."""
    return re.sub(r"(?<=\d)\.(?=\d{3}\b)", "", text)


def _number_found(n: str, source: str) -> bool:
    if re.search(rf"(?<![\d,]){re.escape(n)}(?![\d]|,\d)", source):
        return True
    value = int(n) if n.isdigit() else None
    return value in NUMBER_WORDS and re.search(rf"\b{NUMBER_WORDS[value]}", source.lower()) is not None


def person_names(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """(first, last) of every person in the store: a sentence naming one must find the last name in its sources."""
    if not stories.has_table(conn, "person"):
        return set()
    return {
        (f.split()[0], last)
        for f, last in conn.execute(
            "SELECT first_name, last_name FROM person WHERE first_name IS NOT NULL AND last_name IS NOT NULL"
        )
        if f
    }


def check(
    sentence: dict, sources: dict[str, Source], people: set[tuple[str, str]], fraction: str | None = None
) -> list[str]:
    """The failures of one sentence (empty: it passes)."""
    fails = []
    if not sentence["cites"]:
        return ["keine Quelle genannt"]
    unknown = [c for c in sentence["cites"] if c not in sources]
    if unknown:
        fails.append(f"unbekannte Quelle: {', '.join(unknown)}")
    cited = [sources[c] for c in sentence["cites"] if c in sources]
    if not cited:
        return fails
    text = sentence["text"]
    pool = _norm_numbers("\n".join(s.text for s in cited))
    low = pool.lower()
    for n in _NUMBER.findall(_norm_numbers(text)):
        if not _number_found(n, pool):
            fails.append(f"Zahl {n} nicht in den genannten Quellen")
    body = re.sub(r"^(CDU/CSU|SPD|AfD|Grüne|Linke):\s*", "", text) if fraction else text
    for name, (pattern, needles) in NAMES.items():
        if pattern.search(body) and not any(w in low for w in needles):
            fails.append(f"{name} nicht in den genannten Quellen")
    tokens = re.findall(r"[A-ZÄÖÜ][\wäöüß-]+", text)
    for first, last in people:
        if (
            last in tokens
            and re.search(rf"\b{re.escape(first)}\s+(?:[A-ZÄÖÜ]\.\s+)?{re.escape(last)}\b", text)
            and last.lower() not in low
        ):
            fails.append(f"Name {first} {last} nicht in den genannten Quellen")
    for m in _ROLE_NAME.finditer(text):
        if m.group(1).lower() not in low:
            fails.append(f"Name {m.group(1)} nicht in den genannten Quellen")
    if fraction is not None:
        own = [s for s in cited if s.fraction == fraction]
        if not own:
            fails.append(f"keine Rede der Fraktion {FRACTION_LABEL.get(fraction, fraction)} genannt")
    return fails


def warnings(sentence: dict) -> list[str]:
    n = words(sentence["text"])
    return [f"{n} Wörter (Einfache Sprache: höchstens {MAX_SENTENCE_WORDS})"] if n > MAX_SENTENCE_WORDS else []


# ------------------------------------------------------------------------------------------------------ generation


class Cache:
    """Generated texts by a hash of everything that went into them."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    @staticmethod
    def key(backend: llm.Backend, system: str, documents: list[Document], task: str) -> str:
        blob = json.dumps(
            [backend.name, backend.model, system, [asdict(d) for d in documents], task], ensure_ascii=False
        )
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def get(self, key: str) -> llm.Result | None:
        path = self.folder / f"{key}.json"
        return llm.Result(**json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None

    def put(self, key: str, result: llm.Result) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{key}.json").write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=1), encoding="utf-8"
        )


def _call(backend: llm.Backend, cache: Cache, documents: list[Document], task: str) -> tuple[llm.Result, bool]:
    key = Cache.key(backend, SYSTEM, documents, task)
    if (hit := cache.get(key)) is not None:
        return hit, True
    result = backend.generate(SYSTEM, documents, task)
    cache.put(key, result)
    return result, False


def _fraction_of(sentence: dict) -> str | None:
    m = re.match(r"^(CDU/CSU|SPD|AfD|Grüne|Linke):", sentence["text"])
    return {v: k for k, v in FRACTION_LABEL.items()}[m.group(1)] if m else None


def generate(
    backend: llm.Backend,
    cache: Cache,
    sources: list[Source],
    task: str,
    people: set,
    kind: str,
    length: tuple[int, int] | None = None,
) -> dict:
    """One checked text: the model's attempt, the checks, one retry with the failures if any sentence failed,
    and the sentences that passed. kind "positions" checks each line's Fraktion against its cited speech."""
    by_id = {s.id: s for s in sources}
    documents = [s.document() for s in sources]
    attempts, prompt = [], task
    for attempt in (1, 2):
        result, cached = _call(backend, cache, documents, prompt)
        sentences = parse(result.text)
        for s in sentences:
            fraction = _fraction_of(s) if kind == "positions" else None
            s["checks"] = check(s, by_id, people, fraction if kind != "positions" else (fraction or "?"))
            if kind == "positions" and fraction is None:
                s["checks"].append("Zeile beginnt nicht mit einer Fraktion")
            s["warnings"] = warnings(s)
            s["ok"] = not s["checks"]
            if kind == "positions":
                s["fraction"] = fraction
        n_words = sum(words(s["text"]) for s in sentences if s["ok"])
        length_fail = length is not None and not (length[0] <= n_words <= length[1])
        attempts.append(
            {
                "attempt": attempt,
                "cached": cached,
                "model": result.model,
                "backend": result.backend,
                "cost_usd": result.cost_usd,
                "raw": result.text,
                "words": n_words,
                "length_ok": not length_fail,
                "sentences": sentences,
            }
        )
        failed = [s for s in sentences if not s["ok"]]
        if not failed and not length_fail:
            break
        lines = [f"- „{s['text']}“: {'; '.join(s['checks'])}" for s in failed]
        if length_fail:
            lines.append(f"- Länge: {n_words} geprüfte Wörter, verlangt sind {length[0]} bis {length[1]}.")
        prompt = f"{task}\n\n{RETRY.format(failures=chr(10).join(lines))}"
    final = attempts[-1]
    kept = [
        {k: s[k] for k in ("text", "cites", "paragraph", "warnings") + (("fraction",) if kind == "positions" else ())}
        for s in final["sentences"]
        if s["ok"]
    ]
    return {
        "sentences": kept,
        "words": sum(words(s["text"]) for s in kept),
        "dropped": [{"text": s["text"], "checks": s["checks"]} for s in final["sentences"] if not s["ok"]],
        "attempts": attempts,
        "sources": {s.id: {"url": s.url, "label": s.label} for s in sources},
    }


def _lead_task(payload: dict, top: list[dict]) -> str:
    lines = [
        f"{i}. {s['title']} (Stand: {s['stage']['label'] if s.get('stage') else 'ohne Stand'}; Quellen der "
        f"Woche: {s['quellen']})"
        for i, s in enumerate(top, 1)
    ]
    return TASK_LEAD.format(
        start=german_date(payload["start"]),
        end=german_date(payload["end"]),
        stories="\n".join(lines),
        lo=LEAD_WORDS[0],
        hi=LEAD_WORDS[1],
    )


def build(
    conn: sqlite3.Connection,
    payload: dict,
    backend: llm.Backend,
    cache: Cache,
    positions: bool = True,
    top_n: int = stories.TOP_N,
    log=print,
) -> dict:
    """lead.json for the week of `payload` (stories.json)."""
    top = payload["stories"][:top_n]
    people = person_names(conn)
    lead_sources = unique([s for st in top for s in story_sources(conn, st, max_drucksachen=2)])
    log(f"lead: {len(lead_sources)} sources")
    lead = generate(backend, cache, lead_sources, _lead_task(payload, top), people, "lead", LEAD_WORDS)
    per_story = {}
    for st in top:
        srcs = unique(story_sources(conn, st))
        log(f"kurz {st['id']}: {len(srcs)} sources")
        entry = {
            "title": st["title"],
            "kurz": generate(
                backend,
                cache,
                srcs,
                TASK_KURZ.format(
                    title=st["title"], start=german_date(payload["start"]), end=german_date(payload["end"])
                ),
                people,
                "kurz",
            ),
        }
        reden = _rede_sources(conn, st["reden"]) if positions else []
        fractions = [f for f in FRACTION_LABEL if any(r.fraction == f for r in reden)]
        if len(fractions) >= 2:
            log(f"positions {st['id']}: {len(reden)} speeches")
            task = TASK_POSITIONS.format(title=st["title"], fractions=", ".join(FRACTION_LABEL[f] for f in fractions))
            entry["positions"] = generate(backend, cache, unique(reden), task, people, "positions")
        per_story[st["id"]] = entry
    return {
        "week": payload["week"],
        "start": payload["start"],
        "end": payload["end"],
        "label": LABEL,
        "backend": backend.name,
        "model": backend.model,
        "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "rules": {
            "lead_words": list(LEAD_WORDS),
            "max_sentence_words": MAX_SENTENCE_WORDS,
            "checks": [
                "Quelle genannt",
                "Quelle unter den Eingaben",
                "Zahlen in den genannten Quellen",
                "Fraktionen, Regierung, Bundesrat, Ministerien, Personen in den genannten Quellen",
                "Positionen: Rede der eigenen Fraktion zitiert",
            ],
        },
        "lead": lead,
        "stories": per_story,
    }


def write(
    out: Path,
    conn: sqlite3.Connection,
    payload: dict,
    backend: str = "cli",
    positions: bool = True,
    cache: Path | None = None,
) -> dict:
    """Generates (or takes from the cache) and writes lead.json next to stories.json; returns it with the
    rendered HTML of each text (`html`) for stories.render_section."""
    data = build(conn, payload, llm.backend(backend), Cache(cache or out.parent / "llm-cache"), positions)
    (out / FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return with_html(data)


# ------------------------------------------------------------------------------------------------------ rendering


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def render_sentence(s: dict, sources: dict[str, dict]) -> str:
    links = []
    for c in s["cites"]:
        src = sources.get(c)
        if src:
            links.append(f'<a class="ev" href="{_e(src["url"])}">{_e(src["label"])}</a>')
    cite = f' <span class="cite">[{"; ".join(links)}]</span>' if links else ""
    return f"{_e(s['text'])}{cite}"


def render_text(text: dict) -> str:
    """Sentences of one generated text as HTML, paragraphs as <p>."""
    paras: dict[int, list[str]] = {}
    for s in text["sentences"]:
        paras.setdefault(s["paragraph"], []).append(render_sentence(s, text["sources"]))
    return "".join(f"<p>{' '.join(p)}</p>" for p in paras.values())


def with_html(data: dict) -> dict:
    """lead.json plus `kurz` and `positions` as HTML strings per story, the form stories.render_card takes."""
    out = dict(data)
    out["stories"] = {}
    for sid, e in data["stories"].items():
        kurz = " ".join(render_sentence(s, e["kurz"]["sources"]) for s in e["kurz"]["sentences"])
        pos = [render_sentence(s, e["positions"]["sources"]) for s in e.get("positions", {}).get("sentences", [])]
        out["stories"][sid] = {"kurz": kurz, "positions": pos}
    return out


CSS = """
.radar-lead{border-left:4px solid #7c3aed;background:#faf7ff;padding:10px 14px;border-radius:6px;margin:10px 0}
.radar-lead h2{margin:0 0 6px;font-size:1.15rem}
.radar-lead p{line-height:1.55;margin:6px 0;font-size:1rem}
.radar-lead .cite,.story .cite{font-size:.78rem;color:#5b5670}
.radar-lead a.ev,.story .cite a.ev{color:#2563eb}
.radar-lead .radar-gen{font-size:.78rem;color:#5b5670;margin-top:8px}
"""


def render_section(data: dict) -> str:
    """The lead „Die Woche im Bundestag“ as an HTML fragment, with the label that it is generated."""
    checked = data["lead"]
    dropped = len(checked["dropped"])
    note = f" {dropped} Sätze fielen bei der Prüfung heraus." if dropped else ""
    return (
        f'<section class="radar-lead"><style>{CSS}{stories.CSS}</style><h2>Die Woche im Bundestag</h2>'
        f'{render_text(checked)}<p class="radar-gen">{_e(data["label"])}{_e(note)}</p></section>'
    )
