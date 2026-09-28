"""Read one sitting week of speeches from the foundation store (read-only)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

MIN_CHARS = 500  # shorter units are procedural remarks and single questions, see docs/decisions.md

# speech.fraction is NULL for ministers; person.party fills the gap
PARTY_TO_FRACTION = {"CDU": "CDU/CSU", "CSU": "CDU/CSU", "DIE LINKE.": "Die Linke"}
NO_FRACTION = "ohne Fraktion"  # non-MdB ministers, Länder ministers
MARKERS = {"zwischenfrage", "kurzintervention", "antwort"}  # paragraph kinds whose text is a speech id

EINZELPLAN = {
    "1": "Bundespräsident", "2": "Bundestag", "3": "Bundesrat", "4": "Bundeskanzler", "5": "Auswärtiges Amt",
    "6": "Inneres", "7": "Justiz", "8": "Finanzen", "9": "Wirtschaft und Energie", "10": "Landwirtschaft",
    "11": "Arbeit und Soziales", "12": "Verkehr", "14": "Verteidigung", "15": "Gesundheit", "16": "Umwelt",
    "17": "Bildung, Familie", "19": "Bundesverfassungsgericht", "20": "Bundesrechnungshof",
    "23": "Entwicklung", "24": "Digitales", "25": "Wohnen, Bau", "30": "Forschung", "32": "Bundesschuld",
    "60": "Allgemeine Finanzverwaltung",
}  # fmt: skip

_PROCEDURAL = re.compile(
    r"^((\d+|[a-z]\)|–|ZP\s*\d+)\s*)*"
    r"(Erste|Zweite|Dritte|Beratung|Abgabe|auf Verlangen|Aktuelle Stunde|Wahlvorschl|Vereinbarte Debatte:|"
    r"Beschlussempfehlung|Bericht des|Antrag der|zu dem Antrag|zu der|\(Schluss)",
)
_GESETZ = re.compile(r"^.*?Entwurfs eines (\w+ )?Gesetzes ")
# where the next sub-item of a combined agenda item starts: "b) …", "25 b) …", "ZP 3 …", "2 Erste Beratung …"
_SUB_ITEM = re.compile(r"^((\d+\s*)?[b-z]\)|ZP\s*\d+|\d+\s)")


def connect() -> sqlite3.Connection:
    path = Path(os.environ.get("BDF_DB", "../bundestag-data-foundation/data/bundestag.sqlite"))
    if not path.exists():
        raise FileNotFoundError(f"foundation store not found: {path} (set BDF_DB)")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def week_id(date: str) -> str:
    y, w, _ = dt.date.fromisoformat(date).isocalendar()
    return f"{y}-W{w:02d}"


def week_range(week: str) -> tuple[str, str]:
    y, w = int(week[:4]), int(week[6:])
    monday = dt.date.fromisocalendar(y, w, 1)
    return monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()


def weeks(conn: sqlite3.Connection) -> list[dict]:
    """Every ISO week with sittings: id, sitting numbers, speech count."""
    out: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT st.date, st.number, COUNT(s.id) n FROM sitting st LEFT JOIN speech s ON s.sitting_id = st.id "
        "GROUP BY st.id ORDER BY st.date"
    ):
        wid = week_id(r["date"])
        w = out.setdefault(wid, {"week": wid, "sittings": [], "speeches": 0})
        w["sittings"].append(r["number"])
        w["speeches"] += r["n"]
    return list(out.values())


def short_title(title: str | None, top_id: str) -> str:
    """A label-sized title: the first non-procedural segment of the first sub-item, or the law's name.

    Only the first sub-item counts: in "a) Entwurf eines Gesetzes … | b) Beratung des Antrags … | <Antrag title>"
    the item is about the law, not the motion debated with it. The cards site has a copy (`cards.titles`); change
    both together."""
    if not title:
        if top_id.startswith("Einzelplan"):
            num = top_id.removeprefix("Einzelplan").strip().lstrip("0")
            return f"Haushalt: {EINZELPLAN.get(num, top_id)}" if num else "Haushalt"
        return top_id
    segments = [s.strip() for s in title.split("|") if not s.strip().startswith("(Schluss")]
    if not segments:
        return top_id
    first = next((i for i, s in enumerate(segments) if i and _SUB_ITEM.match(s) and _PROCEDURAL.match(s)),
                 len(segments))  # fmt: skip
    for s in segments[:first]:
        if not _PROCEDURAL.match(s):
            return s
    s = _GESETZ.sub("", segments[0])
    return s[0].upper() + s[1:]


@dataclass
class Speech:
    id: str
    date: str
    person_id: str
    speaker: str
    fraction: str
    role: str | None
    top_id: str
    agenda_item_id: str
    agenda_title: str
    drucksachen: list[str]
    text: str
    pdf_url: str
    source_document_id: str
    part_ids: list[str]
    start: tuple[str, int] = ("", 0)  # (date, position) of the first part
    end: tuple[str, int] = ("", 0)  # … and of the last
    paragraphs: list[tuple[str, str]] = field(default_factory=list)  # (kind, text) incl. interjections and markers
    linked: dict[str, dict] = field(default_factory=dict)  # dropped marker targets (never Zwischenfragen)
    zwischenfrage: bool = False  # another person's turn inside someone else's rede, announced (or taken) as a question
    photo: bool = True  # a portrait exists on the cards site (always True while the store has no person_photo table)
    photo_credit: str | None = None

    @property
    def n_comments(self) -> int:
        return sum(k == "comment" for k, _ in self.paragraphs)


_SQL = """
SELECT s.id, st.date, s.position, s.person_id, s.speaker_name, s.fraction, s.speaker_role, s.text, s.source_document_id,
       st.pdf_url, p.party, a.id AS agenda_item_id, a.top_id, a.title AS agenda_title, a.drucksache_numbers
FROM speech s
JOIN sitting st ON st.id = s.sitting_id
JOIN person p ON p.id = s.person_id
LEFT JOIN agenda_item a ON a.id = s.agenda_item_id
WHERE st.date BETWEEN ? AND ?
ORDER BY st.date, s.position
"""

_SQL_PARAGRAPHS = """
SELECT sp.speech_id, sp.kind, sp.text
FROM speech_paragraph sp JOIN speech s ON s.id = sp.speech_id JOIN sitting st ON st.id = s.sitting_id
WHERE st.date BETWEEN ? AND ?
ORDER BY sp.position
"""


_KURZINTERVENTION = re.compile(r"Kurzintervention|Zwischenbemerkung")


def _marker(main: Speech, other: Speech) -> tuple[str, str]:
    """Another person speaking inside a rede: a Kurzintervention if the chair announced one, else a Zwischenfrage."""
    chair = " ".join(t for k, t in main.paragraphs[-6:] if k == "chair")
    return ("kurzintervention" if _KURZINTERVENTION.search(chair) else "zwischenfrage", other.id)


def _assemble(parts: list[tuple[str, Speech]], by_part: dict[str, list[tuple[str, str]]]) -> None:
    """Paragraphs of one rede's parts, with a marker in the main speech where someone else took the floor
    and a closing marker in that person's speech pointing back to the main one."""
    main = parts[0][1]
    for i, (pid, sp) in enumerate(parts):
        if sp is not main and parts[i - 1][1] is main:
            marks = [j for j, (k, target) in enumerate(main.paragraphs) if k in MARKERS and target == sp.id]
            # "Gestatten Sie …? – Bitte." between two parts of one question is not a second marker
            if not marks or sum(len(t) for k, t in main.paragraphs[marks[-1] + 1 :] if k == "text") >= 200:
                main.paragraphs.append(_marker(main, sp))
        sp.paragraphs.extend(by_part[pid])
    for other in {id(sp): sp for _, sp in parts if sp is not main}.values():
        other.paragraphs.append(("antwort", main.id))
        # no marker when two interrupters follow each other; unannounced turns count as questions (decisions.md)
        kind = next((k for k, target in main.paragraphs if k in MARKERS and target == other.id), "zwischenfrage")
        other.zwischenfrage = kind == "zwischenfrage"


def load_week(conn: sqlite3.Connection, week: str) -> list[Speech]:
    """Speeches of one week with split parts re-joined and short units dropped.

    A rede split at Zwischenfragen (`ID…`, `ID…-2`, …) is re-joined per speaker; where another person spoke
    in between, the main speech gets a ("zwischenfrage"|"kurzintervention", <speech id>) paragraph. Zwischenfragen
    are kept at any length (flag `zwischenfrage`); `linked` holds the marker targets that were dropped (short
    Kurzinterventionen, a main speech too short to keep), so the page can still show them in place."""
    span = week_range(week)
    speeches: list[Speech] = []
    by_base: dict[tuple[str, str], Speech] = {}  # (rede base id, person) -> first part
    rede: dict[str, list[tuple[str, Speech]]] = {}  # rede base id -> its parts in speaking order
    for r in conn.execute(_SQL, span):
        base = re.sub(r"-\d+$", "", r["id"])
        head = by_base.get((base, r["person_id"]))
        if head is not None:
            head.text += "\n\n" + r["text"]
            head.part_ids.append(r["id"])
            head.end = (r["date"], r["position"])
            rede[base].append((r["id"], head))
            continue
        sp = Speech(
            id=r["id"], date=r["date"], person_id=r["person_id"],
            speaker=r["speaker_name"].split(",")[0].split(" (")[0],
            fraction=r["fraction"] or PARTY_TO_FRACTION.get(r["party"], r["party"] or NO_FRACTION),
            role=r["speaker_role"], top_id=r["top_id"], agenda_item_id=r["agenda_item_id"],
            agenda_title=short_title(r["agenda_title"], r["top_id"]),
            drucksachen=json.loads(r["drucksache_numbers"]), text=r["text"], pdf_url=r["pdf_url"],
            source_document_id=r["source_document_id"], part_ids=[r["id"]],
            start=(r["date"], r["position"]), end=(r["date"], r["position"]),
        )  # fmt: skip
        by_base[(base, r["person_id"])] = sp
        rede.setdefault(base, []).append((r["id"], sp))
        speeches.append(sp)

    by_part: dict[str, list[tuple[str, str]]] = {pid: [] for s in speeches for pid in s.part_ids}
    for r in conn.execute(_SQL_PARAGRAPHS, span):
        by_part[r["speech_id"]].append((r["kind"], r["text"]))
    for parts in rede.values():
        _assemble(parts, by_part)

    credits = photos(conn)
    if credits is not None:
        for s in speeches:
            s.photo, s.photo_credit = s.person_id in credits, credits.get(s.person_id)
    # every Zwischenfrage is a point whatever its length (hidden on the map by default, decisions.md); the host
    # speech's page shows it in place from that point. Short Kurzinterventionen are dropped like any short unit.
    kept = [s for s in speeches if len(s.text) >= MIN_CHARS or s.zwischenfrage]
    dropped = {s.id: s for s in speeches if len(s.text) < MIN_CHARS and not s.zwischenfrage}
    for s in kept:
        for kind, target in s.paragraphs:
            if kind in MARKERS and target in dropped:
                d = dropped[target]
                s.linked[target] = {"speaker": d.speaker, "fraction": d.fraction, "paragraphs": d.paragraphs}
    return kept


def has_table(conn: sqlite3.Connection, table: str, column: str | None = None) -> bool:
    """Whether the store has this table (and column): newer foundation tables are optional."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return bool(cols) and (column is None or column in cols)


def photos(conn: sqlite3.Connection) -> dict[str, str | None] | None:
    """Photographer credit per person with a portrait; None if the store predates `person_photo` (then the page
    tries every portrait and hides the ones that fail to load)."""
    if not has_table(conn, "person_photo"):
        return None
    return {r[0]: r[1] for r in conn.execute("SELECT person_id, credit FROM person_photo")}


def vote_page(vote_id: str) -> str:
    """Cards-site path of a decision or roll-call vote page: "21/90/7" -> "abstimmungen/21-90-7.html"."""
    return f"abstimmungen/{vote_id.replace('/', '-')}.html"


def decisions(conn: sqlite3.Connection, agenda_ids: list[str]) -> dict[str, list[dict]]:
    """Announced results per agenda item: {agenda item id: [{id, page, kind, result, subject}]}, in announcement
    order. Reads `decision` and roll-call votes linked by `roll_call_vote.agenda_item_id`; empty if neither exists."""
    out: dict[str, list[dict]] = {}
    ids = sorted(set(agenda_ids))
    marks = ",".join("?" * len(ids))
    seen_votes: set[str] = set()
    if ids and has_table(conn, "decision"):
        for r in conn.execute(
            f"SELECT id, agenda_item_id, kind, result, subject, roll_call_vote_id FROM decision "
            f"WHERE agenda_item_id IN ({marks}) ORDER BY sitting_id, n",
            ids,
        ):
            seen_votes.add(r["roll_call_vote_id"] or "")
            out.setdefault(r["agenda_item_id"], []).append(
                {"id": r["id"], "page": vote_page(r["roll_call_vote_id"] or r["id"]), "kind": r["kind"],
                 "result": r["result"], "subject": r["subject"]}
            )  # fmt: skip
    if ids and has_table(conn, "roll_call_vote", "agenda_item_id"):
        for r in conn.execute(
            f"SELECT id, agenda_item_id, title, yes, no FROM roll_call_vote WHERE agenda_item_id IN ({marks}) "
            f"ORDER BY number",
            ids,
        ):
            if r["id"] in seen_votes:
                continue
            out.setdefault(r["agenda_item_id"], []).append(
                {"id": r["id"], "page": vote_page(r["id"]), "kind": "namentlich",
                 "result": "angenommen" if r["yes"] > r["no"] else "abgelehnt", "subject": r["title"]}
            )  # fmt: skip
    return out
