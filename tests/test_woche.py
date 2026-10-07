import json

from conftest import HIB

from radar import build, period, woche

HIB_ITEM = "INSERT INTO hib_item VALUES (?,?,?,21,?,?,?,?,?,?,?,?,'2026-07-10')"


def _hib(conn):
    conn.executescript(HIB)
    conn.executemany(
        HIB_ITEM,
        [
            ("1001", "350/2026", "2026-07-07", "Mietpreisbremse soll verlängert werden", "Recht", "Antrag", "STO",
             None, "Geschützter Text.", "https://www.bundestag.de/presse/hib/kurzmeldungen-1001", "hib 350/2026"),
            ("1002", "351/2026", "2026-07-08", "Anhörung zur Wohnungsnot", "Bau", "Anhörung", "PEZ",
             "Bauausschuss", "Geschützter Text.", "https://www.bundestag.de/presse/hib/kurzmeldungen-1002",
             "hib 351/2026"),
            ("1003", "351/2026", "2026-07-08", "Kosten der Unterkunft", "Recht", "Antwort", None, None,
             "Geschützter Text.", "https://www.bundestag.de/presse/hib/kurzmeldungen-1003", "hib 351/2026"),
            ("900", "300/2026", "2026-06-30", "Eine Woche davor", "Recht", "Antwort", None, None, "x",
             "https://www.bundestag.de/presse/hib/kurzmeldungen-900", "hib 300/2026"),
        ],
    )  # fmt: skip
    conn.executemany("INSERT INTO hib_drucksache VALUES (?,?,?)", [("1001", "21/100", 1), ("1001", "21/8309", 2)])


def test_without_hib(conn, tmp_path):
    assert woche.latest_week(conn) is None and woche.load(conn) is None
    (tmp_path / woche.FILE).write_text("{}")
    assert woche.write(tmp_path, conn) is None
    assert not (tmp_path / woche.FILE).exists()  # an old week does not stay on the page


def test_load_newest_week(conn):
    _hib(conn)
    conn.executescript(
        """
        CREATE TABLE drucksache (id TEXT PRIMARY KEY, number TEXT, type TEXT, title TEXT, date TEXT, pdf_url TEXT,
            originators TEXT);
        CREATE TABLE vorgang_drucksache (vorgang_id TEXT, drucksache_id TEXT);
        CREATE TABLE vorgang (id TEXT PRIMARY KEY, type TEXT, initiators TEXT);
        INSERT INTO drucksache VALUES ('d1', '21/100', 'Antrag', 'Mietpreisbremse verlängern', '2026-07-01', 'u',
            '["CDU/CSU", "SPD"]');
        INSERT INTO vorgang_drucksache VALUES ('v1', 'd1');
        INSERT INTO vorgang VALUES ('v1', 'Antrag', '["Fraktion der SPD", "Fraktion der CDU/CSU"]');
        """
    )
    assert woche.latest_week(conn, today="2026-07-09") == "2026-W28"
    assert woche.latest_week(conn, today="2026-07-01") == "2026-W27"  # nothing from the future
    w = woche.load(conn, today="2026-07-09")
    assert (w["week"], w["start"], w["end"], w["current"]) == ("2026-W28", "2026-07-06", "2026-07-12", True)
    assert w["sittings"] == ["2026-07-08", "2026-07-09"]
    assert [i["id"] for i in w["items"]] == ["1003", "1002", "1001"]  # newest first
    assert "text" not in json.dumps(w)  # hib texts are protected: never on the page
    assert w["items"][1]["committee"] == "Bauausschuss" and "committee" not in w["items"][0]
    ds = w["items"][2]["drucksachen"]
    assert [d["number"] for d in ds] == ["21/100", "21/8309"]  # in hib's order
    assert ds[0]["vorgang"] == "v1" and ds[0]["pdf"] == "u" and ds[0]["type"] == "Antrag"
    assert ds[0]["page"] == "vorgaenge/v1.html"
    assert w["items"][2]["einbringer"] == ["CDU/CSU", "SPD"]
    assert {i["area"] for i in w["items"]} <= set(woche.RESSORTS_OF_AREA) | {
        "weitere"
    }  # the Vorgang's initiators, in chip order
    assert w["items"][0]["einbringer"] == []  # no Drucksache in the store
    assert ds[0]["plenum"] == [{"agenda_id": "21/88/2", "top": "Tagesordnungspunkt 2", "date": "2026-07-08",
                                "week": "2026-W28"}]  # fmt: skip
    assert ds[1] == {"number": "21/8309", "pdf": "https://dserver.bundestag.de/btd/21/083/2108309.pdf", "plenum": []}
    assert woche.load(conn, today="2026-07-14")["current"] is False  # the newest items are from last week


def test_index_page_with_woche(conn, tmp_path):
    _hib(conn)
    assert woche.write(tmp_path, conn, today="2026-07-09") == 3
    payload = {"week": "2026-W28", "clusters": [], "speeches": [{"agenda_id": "21/88/2", "date": "2026-07-08"}]}
    (tmp_path / "2026-W28.json").write_text(json.dumps(payload))
    page = build.index_page(tmp_path)
    start = page.index("const DATA = ") + len("const DATA = ")
    data = json.loads(page[start : page.index(";\n", start)].replace("<\\/", "</"))
    assert data["woche"]["week"] == "2026-W28"
    assert data["woche"]["items"][2]["drucksachen"][0]["plenum"][0]["map"] is True  # the item's speeches are mapped
    assert "heute im bundestag" in page[page.index('class="radar-note"') :]  # the method note names hib
    (tmp_path / woche.FILE).unlink()
    page = build.index_page(tmp_path)
    assert '"woche": null' in page and "heute im bundestag“ (hib) der neuesten" not in page


def test_research_path_by_type():
    assert woche.research_path("v1", "Antrag") == "vorgaenge/v1.html"
    assert woche.research_path("v2", "Kleine Anfrage") == "regierung/anfragen/v2.html"
    assert woche.research_path("v3", "Große Anfrage") == "regierung/anfragen/v3.html"
    assert woche.research_path("v4", "Schriftliche Frage") == "regierung/fragen/v4.html"
    assert woche.research_path("v5", None) == "vorgaenge/v5.html"


def test_einbringer_groups():
    assert [woche.einbringer(o) for o in ("AfD", "Die Linke", "BÜNDNIS 90/DIE GRÜNEN")] == [
        "AfD", "Die Linke", "BÜNDNIS 90/DIE GRÜNEN"]  # fmt: skip
    assert woche.einbringer("Bundesministerium der Finanzen") == "Bundesregierung"
    assert woche.einbringer("Bundesregierung") == "Bundesregierung"
    assert woche.einbringer("Bayern") == woche.einbringer("Bundesrat") == "Bundesrat"
    assert woche.einbringer("Präsidentin des Deutschen Bundestages") == "Sonstige"


def test_area_of_ressort():
    assert woche.AREA_OF_RESSORT["Inneres"] == "sicherheit"
    assert woche.AREA_OF_RESSORT["Auswärtiges"] == woche.AREA_OF_RESSORT["Verteidigung"] == "aussen"
    assert woche.AREA_OF_RESSORT["Gesundheit"] == "gesundheit"
    assert set(woche.RESSORTS_OF_AREA) <= set(period.AREAS)  # the page colours tiles by period's areas


def test_plenum_topics():
    payload = {"week": "2026-W39", "clusters": [{"id": 1, "terms": ["rente", "alter", "x"], "n": 5},
                                                {"id": 2, "terms": ["bahn", "schiene"], "n": 9}]}  # fmt: skip
    assert build.plenum_topics(payload) == {"week": "2026-W39", "topics": [
        {"id": 2, "label": "Bahn · Schiene", "n": 9}, {"id": 1, "label": "Rente · Alter", "n": 5}]}  # fmt: skip
