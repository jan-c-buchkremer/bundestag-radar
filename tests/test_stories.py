import json
import sqlite3

import pytest

from radar import stories

STORE = """
CREATE TABLE sitting (id TEXT PRIMARY KEY, date TEXT, preliminary INTEGER DEFAULT 0);
CREATE TABLE hib_item (id TEXT PRIMARY KEY, number TEXT, date TEXT, title TEXT, ressort TEXT, kind TEXT,
    committee TEXT, text TEXT, source_url TEXT);
CREATE TABLE hib_drucksache (hib_id TEXT, drucksache_number TEXT, position INTEGER);
CREATE TABLE drucksache (id TEXT PRIMARY KEY, number TEXT, type TEXT, title TEXT, date TEXT, pdf_url TEXT,
    publisher TEXT, originators TEXT);
CREATE TABLE vorgang (id TEXT PRIMARY KEY, type TEXT, title TEXT, status TEXT, subjects TEXT, initiators TEXT);
CREATE TABLE vorgang_drucksache (vorgang_id TEXT, drucksache_id TEXT);
CREATE TABLE vorgang_referral (vorgang_id TEXT, committee TEXT, lead INTEGER);
CREATE TABLE agenda_item (id TEXT PRIMARY KEY, sitting_id TEXT, position INTEGER, top_id TEXT, title TEXT,
    kind TEXT, no_debate INTEGER DEFAULT 0, drucksache_numbers TEXT DEFAULT '[]');
CREATE TABLE agenda_item_vorlage (id TEXT PRIMARY KEY, agenda_item_id TEXT, sub_item_id TEXT,
    drucksache_number TEXT, vorgang_id TEXT);
CREATE TABLE speech (id TEXT PRIMARY KEY, sitting_id TEXT, agenda_item_id TEXT, sub_item_id TEXT, position INTEGER,
    person_id TEXT, speaker_name TEXT, speaker_group TEXT, kind TEXT DEFAULT 'rede', rede_id TEXT,
    interruption TEXT, text TEXT);
CREATE TABLE decision (id TEXT PRIMARY KEY, sitting_id TEXT, agenda_item_id TEXT, position INTEGER, kind TEXT,
    subject TEXT, result TEXT, roll_call_vote_id TEXT, vorgang_id TEXT, drucksache_number TEXT);
CREATE TABLE decision_vorgang (decision_id TEXT, vorgang_id TEXT, via TEXT);
CREATE TABLE decision_fraction (decision_id TEXT, fraction TEXT, position TEXT);
CREATE TABLE roll_call_vote (id TEXT PRIMARY KEY, sitting_id TEXT, title TEXT, vorgang_id TEXT, yes INTEGER,
    no INTEGER, abstain INTEGER, date TEXT);
CREATE TABLE person (id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT);

INSERT INTO sitting VALUES ('21/99', '2026-10-08', 0);
INSERT INTO vorgang VALUES
  ('300001', 'Gesetzgebung', 'Gesetz zur Besteuerung von Immobilienkonzernen', 'Überwiesen', '["Steuern"]',
   '["Fraktion BÜNDNIS 90/DIE GRÜNEN"]'),
  ('300002', 'Antrag', 'Steuergerechtigkeit herstellen', 'Beschlussempfehlung liegt vor', '["Steuern"]',
   '["Fraktion Die Linke"]'),
  ('300003', 'Kleine Anfrage', 'Ausbau der Mobilfunkmasten im ländlichen Raum', 'Beantwortet', '[]',
   '["Fraktion der AfD"]');
INSERT INTO drucksache VALUES
  ('d1', '21/100', 'Gesetzentwurf', 'Entwurf eines Gesetzes zur Besteuerung', '2026-10-06', 'pdf100', 'BT', '[]'),
  ('d2', '21/101', 'Antrag', 'Steuergerechtigkeit herstellen', '2026-03-01', NULL, 'BT', '[]'),
  ('d3', '21/102', 'Kleine Anfrage', 'Mobilfunkmasten', '2026-10-07', NULL, 'BT', '[]'),
  ('d9', '21/101', 'Drucksache', 'Bundesrat, same number', '2026-10-07', NULL, 'BR', '[]');
INSERT INTO vorgang_drucksache VALUES ('300001', 'd1'), ('300002', 'd2'), ('300003', 'd3');
INSERT INTO vorgang_referral VALUES ('300001', 'Finanzausschuss', 1), ('300001', 'Haushaltsausschuss', 0);
INSERT INTO hib_item VALUES
  ('1001', '787/2026', '2026-10-06', 'AfD fragt nach Mobilfunkmasten', 'Digitales', 'Kleine Anfrage', NULL,
   'Die AfD fragt nach dem Ausbau der Mobilfunkmasten.', 'u1001'),
  ('1002', '787/2026', '2026-10-06', 'Grüne wollen Immobilienkonzerne besteuern', 'Finanzen', 'Gesetzentwurf',
   NULL, 'Der Entwurf …', 'u1002'),
  ('1003', '790/2026', '2026-10-07', 'Finanzausschuss berät Besteuerung der Immobilienkonzerne', 'Finanzen',
   'Ausschuss', 'Finanzausschuss', 'Der Ausschuss …', 'u1003'),
  ('1004', '791/2026', '2026-10-08', 'Experten zur Olympiabewerbung', 'Sport und Ehrenamt', 'Anhörung',
   'Sportausschuss', '…', 'u1004'),
  ('1005', '792/2026', '2026-10-09', 'Neuer Antrag zu Radwegen', 'Verkehr', 'Antrag', NULL, '…', 'u1005'),
  ('1006', '793/2026', '2026-10-09', '„Das Parlament“: Die Sitzungswoche', 'Bundestagsnachrichten', 'Bericht',
   NULL, '…', 'u1006'),
  ('0999', '700/2026', '2026-09-30', 'last week', 'Finanzen', 'Antrag', NULL, '…', 'u999');
INSERT INTO hib_drucksache VALUES ('1001', '21/100', 1), ('1001', '21/102', 2), ('1002', '21/100', 1),
  ('1005', '21/999', 1), ('1006', '21/101', 1);
INSERT INTO agenda_item VALUES
  ('21/99/2', '21/99', 2, 'Zusatzpunkt 3', 'Beratung des Gesetzentwurfs | Gesetz zur Besteuerung', NULL, 0, '[]'),
  ('21/99/3', '21/99', 3, 'Zusatzpunkt 5', 'Aktuelle Stunde | auf Verlangen der Fraktion der AfD | Energiepreise',
   'aktuelle_stunde', 0, '[]'),
  ('21/99/4', '21/99', 4, 'Tagesordnungspunkt 42', 'Überweisungen im vereinfachten Verfahren', NULL, 1, '[]');
INSERT INTO agenda_item_vorlage VALUES ('a1', '21/99/2', NULL, '21/100', '300001'),
  ('a2', '21/99/2', NULL, '21/101', '300002'), ('a3', '21/99/4', NULL, '21/102', '300003');
INSERT INTO speech (id, sitting_id, agenda_item_id, position, person_id, speaker_name, speaker_group, rede_id,
    interruption, text) VALUES
  ('R1', '21/99', '21/99/2', 1, 'p1', 'Katharina Beck (BÜNDNIS 90/DIE GRÜNEN)', 'BÜNDNIS 90/DIE GRÜNEN', 'R1',
   NULL, 'a'),
  ('R1-2', '21/99', '21/99/2', 2, 'p3', 'Olav Gutting (CDU/CSU)', 'CDU/CSU', 'R1', 'zwischenfrage', 'b'),
  ('R2', '21/99', '21/99/2', 3, 'p2', 'Olav Gutting (CDU/CSU)', 'CDU/CSU', 'R2', NULL, 'c'),
  ('R3', '21/99', '21/99/3', 4, 'p4', 'Diana Zimmer (AfD)', 'AfD', 'R3', NULL, 'd');
INSERT INTO decision VALUES
  ('21/99/5', '21/99', '21/99/2', 1, 'namentlich', 'Gesetzentwürfe', 'abgelehnt', '21/99/5', NULL, NULL),
  ('21/99/p9', '21/99', '21/99/2', 2, 'handzeichen', 'Beschlussempfehlung', 'angenommen', NULL, NULL, NULL);
INSERT INTO decision_vorgang VALUES ('21/99/5', '300001', 'dip_step'), ('21/99/5', '300009', 'drucksache'),
  ('21/99/p9', '300002', 'drucksache');
INSERT INTO decision_fraction VALUES ('21/99/5', 'CDU/CSU', 'no'), ('21/99/5', 'BÜNDNIS 90/DIE GRÜNEN', 'yes');
INSERT INTO roll_call_vote VALUES ('21/99/5', '21/99', 'Gesetzentwurf zur Immobilienbesteuerung', NULL, 131, 446,
  0, '2026-10-08'), ('21/99/10', '21/99', 'Entschließung zur Schienenstrecke', NULL, 416, 131, 2, '2026-10-08');
"""


@pytest.fixture
def store():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(STORE)
    return c


def by_id(payload):
    return {s["id"]: s for s in payload["stories"]}


def test_story_follows_a_vorgang_across_sources(store):
    p = stories.load(store, "2026-W41")
    assert (p["week"], p["start"], p["end"]) == ("2026-W41", "2026-10-05", "2026-10-11")
    top = p["stories"][0]
    assert top["id"] == "v300001"  # the Gesetzgebung names the story, the Antrag debated with it joins
    assert [v["id"] for v in top["vorgaenge"]] == ["300001", "300002"]
    assert top["signals"] == ["hib", "drucksache", "debatte", "abstimmung", "namentlich"]
    assert top["quellen"] == "hib, Drucksache, Debatte, Abstimmung, namentliche Abstimmung"
    assert top["score"] == 5 and "rank" not in top
    assert top["stage"] == {"label": "In den Ausschüssen", "dip": "Überwiesen"}
    assert top["einbringer"] == ["BÜNDNIS 90/DIE GRÜNEN", "Die Linke"]
    assert top["url"] == "https://plenar-radar.de/vorgaenge/300001.html"
    assert [h["id"] for h in top["hib"]] == ["1002", "1003"]  # 1001 is about the Kleine Anfrage, 21/100 is context
    assert top["hib"][1]["via"].startswith("Ausschuss")  # no Drucksache: joined by committee and title words
    assert [r["id"] for r in top["reden"]] == ["R1", "R2"]  # Reden, no Zwischenfrage
    assert top["reden"][0] == {"id": "R1", "speaker": "Katharina Beck", "fraction": "BÜNDNIS 90/DIE GRÜNEN",
                               "person_id": "p1", "agenda_id": "21/99/2",
                               "url": "https://plenar-radar.de/reden/R1.html"}  # fmt: skip
    assert top["plenum"][0]["url"] == "https://plenar-radar.de/sitzungen/21-99.html#top-2"
    assert top["plenum"][0]["fraktionen"] == {"BÜNDNIS 90/DIE GRÜNEN": 1, "CDU/CSU": 1}
    vote = top["abstimmungen"][0]
    assert (vote["id"], vote["yes"], vote["no"], vote["result"]) == ("21/99/5", 131, 446, "abgelehnt")
    assert vote["url"] == "https://plenar-radar.de/abstimmungen/21-99-5.html"  # two Vorgänge: its own page
    assert vote["fraktionen"] == {"CDU/CSU": "no", "BÜNDNIS 90/DIE GRÜNEN": "yes"}
    assert top["abstimmungen"][1]["url"] == "https://plenar-radar.de/vorgaenge/300002.html#abst-21-99-p9"
    assert [d["number"] for d in top["drucksachen"]] == ["21/100"]  # dated in the week (or linked by hib)


def test_standalone_stories_and_exclusions(store):
    s = by_id(stories.load(store, "2026-W41"))
    assert [h["id"] for h in s["v300003"]["hib"]] == ["1001"]
    assert s["v300003"]["url"] == "https://plenar-radar.de/regierung/anfragen/300003.html"
    assert s["v300003"]["signals"] == ["hib", "drucksache"]  # on the agenda without debate: no Debatte
    assert s["ds-21-999"]["title"] == "Neuer Antrag zu Radwegen"  # DIP has not delivered the Drucksache yet
    assert s["ds-21-999"]["drucksachen"][0]["pdf"] == "https://dserver.bundestag.de/btd/21/009/2100999.pdf"
    assert s["hib-1004"]["signals"] == ["hib"]  # committee report that matches no story
    assert s["top-21-99-3"]["title"] == "Aktuelle Stunde: Energiepreise"
    assert s["top-21-99-3"]["einbringer"] == ["AfD"]  # who asked for it
    assert s["abst-21-99-10"]["signals"] == ["abstimmung", "namentlich"]  # roll call without a Vorgang
    assert not any(h["id"] in ("1006", "0999") for st in s.values() for h in st["hib"])  # overview; last week
    assert '"text"' not in json.dumps(list(s.values()))  # hib texts stay out of stories.json


def test_order_is_by_kinds_of_source(store):
    p = stories.load(store, "2026-W41")
    scores = [s["score"] for s in p["stories"]]
    assert scores == sorted(scores, reverse=True)
    assert p["counts"]["by_score"][5] == 1


def test_bundle_agenda_items_do_not_join(store):
    store.execute("UPDATE agenda_item SET no_debate = 0 WHERE id = '21/99/4'")  # still no Reden: no join
    assert "v300003" in by_id(stories.load(store, "2026-W41"))


def test_stems_and_stage():
    assert "immobi" in stories.stems("Immobilienkonzerne besteuern") and "fraktion" not in stories.stems("Fraktion")
    assert stories.stage("Antrag", "Angenommen") == {"label": "Angenommen", "dip": "Angenommen"}
    assert stories.stage("Gesetzgebung", "Verabschiedet")["label"] == "Im Bundestag beschlossen"
    assert stories.stage("Antrag", None) is None


def test_render_section(store, tmp_path):
    p = stories.write(tmp_path, store, "2026-W41")
    assert (tmp_path / stories.FILE).exists()
    page = stories.render_section(p, {"stories": {"v300001": {"kurz": "Kurz <b>x</b>", "positions": ["SPD: y"]}}})
    assert "Quellen: hib, Drucksache, Debatte, Abstimmung, namentliche Abstimmung" in page
    assert 'href="https://plenar-radar.de/vorgaenge/300001.html"' in page and "Positionen in der Debatte" in page
    assert '<span class="chip gru">Grüne</span>' in page
    assert "<script" not in page
    assert "Die Woche im Bundestag" in stories.lab_page(p)
