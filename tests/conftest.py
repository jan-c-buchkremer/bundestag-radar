import re
import sqlite3

import pytest

# What the foundation's ingest derives (bdf ingest_groups, ingest_speech_parts), as triggers over the rows a test
# inserts; speech.interruption: derive() below
DERIVED = """
CREATE TRIGGER agenda_kind AFTER INSERT ON agenda_item BEGIN
  UPDATE agenda_item SET kind = CASE WHEN NEW.title LIKE 'Befragung der Bundesregierung%' THEN 'befragung'
    WHEN NEW.title LIKE 'Fragestunde%' THEN 'fragestunde' WHEN NEW.title LIKE 'Aktuelle Stunde%' THEN 'aktuelle_stunde'
    END WHERE id = NEW.id;
END;
CREATE TRIGGER speech_derived AFTER INSERT ON speech BEGIN
  UPDATE speech SET
    speaker_group = CASE
      WHEN NEW.speaker_role LIKE '%(%)' THEN 'Bundesrat'
      WHEN NEW.speaker_role LIKE 'Bundeskanzler%' OR NEW.speaker_role LIKE 'Bundesminister%'
        OR NEW.speaker_role LIKE 'Staatsminister%' OR NEW.speaker_role LIKE 'Parl%Staatssekret%' THEN 'Bundesregierung'
      ELSE coalesce(NEW.fraction, 'Sonstige') END,
    member_fraction = coalesce((SELECT name FROM membership WHERE person_id = NEW.person_id AND kind = 'fraction'
                                ORDER BY to_date IS NULL DESC, from_date DESC LIMIT 1), NEW.fraction),
    rede_id = CASE WHEN NEW.id GLOB '*-[0-9]*' THEN substr(NEW.id, 1, instr(NEW.id, '-') - 1) ELSE NEW.id END
  WHERE id = NEW.id;
END;
"""

SCHEMA = (
    """
CREATE TABLE person (id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, party TEXT, is_mdb INTEGER);
CREATE TABLE sitting (id TEXT PRIMARY KEY, wahlperiode INTEGER, number INTEGER, date TEXT, pdf_url TEXT);
CREATE TABLE membership (person_id TEXT, wahlperiode INTEGER, kind TEXT, name TEXT, from_date TEXT, to_date TEXT);
CREATE TABLE agenda_item (id TEXT PRIMARY KEY, sitting_id TEXT, top_id TEXT, title TEXT, drucksache_numbers TEXT,
    kind TEXT);
CREATE TABLE speech (id TEXT PRIMARY KEY, sitting_id TEXT, agenda_item_id TEXT, position INTEGER, person_id TEXT,
    speaker_name TEXT, speaker_role TEXT, fraction TEXT, text TEXT, source_document_id TEXT,
    speaker_group TEXT, member_fraction TEXT, rede_id TEXT, interruption TEXT, interruption_start TEXT);
CREATE TABLE speech_paragraph (id TEXT PRIMARY KEY, speech_id TEXT, position INTEGER, kind TEXT, text TEXT);
"""
    + DERIVED
)

LONG = "Wohnen ist die soziale Frage unserer Zeit. " * 15  # > 500 chars
SPEECH = ("INSERT INTO speech (id, sitting_id, agenda_item_id, position, person_id, speaker_name, speaker_role, "
          "fraction, text, source_document_id) VALUES (?,?,?,?,?,?,?,?,?,?)")  # fmt: skip
AGENDA_ITEM = "INSERT INTO agenda_item (id, sitting_id, top_id, title, drucksache_numbers) VALUES (?,?,?,?,?)"


def derive(c: sqlite3.Connection) -> None:
    """speech.interruption and interruption_start by the foundation's rule (bdf ingest_speech_parts): a part by
    someone other than the rede's first speaker is a Kurzintervention when the chair's words among the last six
    paragraphs of the part before announce one, else a Zwischenfrage; the same person again is a new interruption
    only after 30 words of the main speaker; none in a Befragung or Fragestunde. Call after inserting a rede."""
    turns = {r[0] for r in c.execute("SELECT id FROM agenda_item WHERE kind IN ('befragung', 'fragestunde')")}
    chair: dict[str, list[str]] = {}
    for sid, kind, text in c.execute("SELECT speech_id, kind, text FROM speech_paragraph ORDER BY speech_id, position"):
        chair.setdefault(sid, []).append(text if kind == "chair" else "")
    redes: dict[str, list] = {}
    for r in c.execute("SELECT id, person_id, text, agenda_item_id, rede_id FROM speech ORDER BY sitting_id, position"):
        redes.setdefault(r[4], []).append(r)
    updates = []
    for parts in redes.values():
        main, since_main, last, prev = parts[0][1], 0, {}, None
        for p in parts:
            kind = start = None
            if p[1] == main or p[3] in turns:
                since_main += len(p[2].split())
            elif prev is not None and prev[1] == main:
                if p[1] not in last or since_main >= 30:
                    announced = re.search(r"Kurzintervention|Zwischenbemerkung", " ".join(chair.get(prev[0], [])[-6:]))
                    last[p[1]] = ("kurzintervention" if announced else "zwischenfrage", p[0])
                kind, start = last[p[1]]
                since_main = 0
            else:
                kind, start = last.setdefault(p[1], ("zwischenfrage", p[0]))
            updates.append((kind, start, p[0]))
            prev = p
    c.executemany("UPDATE speech SET interruption = ?, interruption_start = ? WHERE id = ?", updates)


PLPR = "BT-PlPr. 21/88"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.executemany(
        "INSERT INTO person VALUES (?,?,?,?,?)",
        [
            ("1", "Anna", "Adler", "SPD", 1),
            ("2", "Bernd", "Berg", "CSU", 1),
            ("3", "Clara", "Cohn", "DIE LINKE.", 1),
            ("9", "Stefanie", "Hubig", None, 0),
        ],
    )
    c.executemany(
        "INSERT INTO sitting VALUES (?,?,?,?,?)",
        [
            ("21/88", 21, 88, "2026-07-08", "https://x/21088.pdf"),
            ("21/89", 21, 89, "2026-07-09", "https://x/21089.pdf"),
            ("21/91", 21, 91, "2026-09-08", "https://x/21091.pdf"),
        ],
    )
    c.execute("INSERT INTO membership VALUES ('1', 21, 'fraction', 'SPD', '2025-03-25', NULL)")
    c.executemany(
        AGENDA_ITEM,
        [
            ("21/88/1", "21/88", "Tagesordnungspunkt 1", "Befragung der Bundesregierung", "[]"),
            (
                "21/88/2",
                "21/88",
                "Tagesordnungspunkt 2",
                "Beratung des Antrags der Abgeordneten X, Y und der Fraktion Die Linke | Mietpreisbremse verlängern",
                '["21/100"]',
            ),
            ("21/91/1", "21/91", "Einzelplan 04", None, "[]"),
        ],
    )
    # a Zwischenfrage: rede ID1 split into ID1 (Berg), ID1-2 (Cohn asks), ID1-3 (Berg answers)
    c.executemany(
        SPEECH,
        [
            ("ID0", "21/88", "21/88/1", 1, "9", "Stefanie Hubig, Bundesministerin", "Bundesministerin der Justiz",
             None, LONG, PLPR),
            ("ID1", "21/88", "21/88/2", 2, "2", "Bernd Berg (CDU/CSU)", None, "CDU/CSU", LONG, PLPR),
            ("ID1-2", "21/88", "21/88/2", 3, "3", "Clara Cohn (Die Linke)", None, "Die Linke", "Kurze Frage?", PLPR),
            ("ID1-3", "21/88", "21/88/2", 4, "2", "Bernd Berg (CDU/CSU)", None, "CDU/CSU", "Kurze Antwort.", PLPR),
            ("ID2", "21/89", "21/88/2", 1, "1", "Anna Adler, Bundesministerin", "Bundesministerin für Bauwesen", None,
             LONG, "BT-PlPr. 21/89"),
            ("ID3", "21/91", "21/91/1", 1, "1", "Anna Adler (SPD)", None, "SPD", LONG, "BT-PlPr. 21/91"),
        ],
    )  # fmt: skip
    c.executemany(
        "INSERT INTO speech_paragraph VALUES (?,?,?,?,?)",
        [
            ("ID1/1", "ID1", 1, "text", LONG),
            ("ID1/2", "ID1", 2, "comment", "(Beifall)"),
            ("ID1/3", "ID1", 3, "chair", "Zwischenfrage?"),
            ("ID1-2/1", "ID1-2", 1, "text", "Kurze Frage?"),
            ("ID1-3/1", "ID1-3", 1, "text", "Kurze Antwort."),
            ("ID0/1", "ID0", 1, "text", LONG),
            ("ID2/1", "ID2", 1, "text", LONG),
            ("ID3/1", "ID3", 1, "text", LONG),
        ],
    )
    derive(c)
    return c
