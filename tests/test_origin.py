import json

from landscape import corpus, origin


def test_episodes_need_a_gap_before_they_start():
    on, off = 0.1, 0.0
    # active at 0–1 (start of period), dip of 2 weeks (bridged), gap of 4 → new episode at 9, gap of 3 → bridged
    shares = [on, on, off, off, on, off, off, off, off, on, off, off, off, on]
    assert origin.episodes(shares, threshold=0.05, gap=4) == [
        {"start": 0, "end": 4, "period_start": True},
        {"start": 9, "end": 13, "period_start": False},
    ]
    # a first episode after ≥ gap quiet weeks has an observable lead-up
    assert origin.episodes([off] * 4 + [on], 0.05, 4) == [{"start": 4, "end": 4, "period_start": False}]
    assert origin.episodes([off, off, on], 0.05, 4)[0]["period_start"] is True
    assert origin.episodes([0.049, 0.05], 0.05, 4) == [{"start": 1, "end": 1, "period_start": True}]


def test_fraction_names_and_aktuelle_stunde():
    assert origin.fraction_name("Fraktion der AfD") == "AfD"
    assert origin.fraction_name("Fraktion DIE LINKE") == "Die Linke"
    assert origin.fraction_name("Fraktion BÜNDNIS 90/DIE GRÜNEN") == "BÜNDNIS 90/DIE GRÜNEN"
    assert origin.fraction_name("Bundesregierung") == "Bundesregierung"
    t = "Aktuelle Stunde | auf Verlangen der Fraktionen der CDU/CSU und SPD | Lage im Nahen und Mittleren Osten"
    assert origin.aktuelle_stunde(t) == ["CDU/CSU", "SPD"]
    assert origin.aktuelle_stunde("Aktuelle Stunde | auf Verlangen der Fraktion Die Linke | Gaza") == ["Die Linke"]
    assert origin.aktuelle_stunde("Aktuelle Stunde | auf Verlangen der Fraktion BÜNDNIS 90/DIE GRÜNEN | Dürre") == [
        "BÜNDNIS 90/DIE GRÜNEN"
    ]
    assert origin.aktuelle_stunde("Beratung des Antrags | Mietpreisbremse") is None
    assert origin.aktuelle_stunde(None) is None


def _speech(sid, agenda, fraction="SPD", words=10):
    return corpus.Speech(
        id=sid, date="2026-01-01", person_id="1", speaker=f"Rednerin {sid}", fraction=fraction, role=None, top_id="T",
        agenda_item_id=agenda, agenda_title=f"Punkt {agenda}", drucksachen=[], text="wort " * words, pdf_url="",
        source_document_id="", part_ids=[sid],
    )  # fmt: skip


def test_origins_measures(conn):
    conn.executescript(
        """
        CREATE TABLE drucksache (id TEXT PRIMARY KEY, number TEXT, type TEXT, originators TEXT, pdf_url TEXT);
        CREATE TABLE vorgang (id TEXT PRIMARY KEY, initiators TEXT);
        CREATE TABLE vorgang_drucksache (vorgang_id TEXT, drucksache_id TEXT);
        INSERT INTO drucksache VALUES ('d1', '21/100', 'Beschlussempfehlung und Bericht', '["Innenausschuss"]', 'u');
        INSERT INTO drucksache VALUES ('d2', '21/200', 'Gesetzentwurf', '["Bundesregierung"]', NULL);
        INSERT INTO vorgang VALUES ('v1', '["Fraktion DIE LINKE"]');
        INSERT INTO vorgang VALUES ('v2', '[]');
        INSERT INTO vorgang_drucksache VALUES ('v1', 'd1'), ('v2', 'd2');
        INSERT INTO agenda_item VALUES ('A', 's', 'TOP 1', 'Beratung | Mieten', '["21/100", "21/200"]');
        INSERT INTO agenda_item VALUES ('S', 's', 'ZP 1',
            'Aktuelle Stunde | auf Verlangen der Fraktion der AfD | Mieten', '[]');
        """
    )
    weeks = [f"W{i}" for i in range(10)]
    rows, assign = [], {w: {} for w in weeks}

    def add(week, sid, agenda, theme, fraction="SPD", words=10):
        rows.append((week, _speech(sid, agenda, fraction, words)))
        assign[week][sid] = theme

    for i, w in enumerate(weeks):  # theme 0 (other debates) fills every week
        add(w, f"x{i}a", "X", 0, "CDU/CSU")
        add(w, f"x{i}b", "X", 0, "SPD")
    # lead-up: theme 1 mentioned inside debate X by Die Linke in W4 and W5, small (below the active share)
    for i in range(8):
        add(f"W{i}", f"f{i}", "X", 0, "AfD")
    add("W4", "m1", "X", 1, "Die Linke", words=1)
    add("W5", "m2", "X", 1, "Die Linke", words=1)
    # W8: theme 1 gets its own agenda items A and S
    for sid, agenda in (("a1", "A"), ("a2", "A"), ("s1", "S"), ("s2", "S")):
        add("W8", sid, agenda, 1, "AfD")
    model = {"themes": [{"id": 0}, {"id": 1}], "assign": assign}
    out = origin.origins(conn, rows, model, weeks)
    assert out["params"] == {"active_share": origin.ACTIVE_SHARE, "gap": origin.GAP, "lookback": origin.LOOKBACK}
    t1 = out["themes"][1]["episodes"]
    assert [(e["start"], e["end"], e["period_start"]) for e in t1] == [("W8", "W8", False)]
    formal = t1[0]["formal"]
    # the Beschlussempfehlung counts for the Vorgang's initiator, the Regierungsentwurf for its urheber
    items = {it["agenda_id"]: it for it in formal["items"]}
    assert items["A"]["originators"] == ["Die Linke", "Bundesregierung"]
    first = items["A"]["vorlagen"][0]
    assert first == {
        "number": "21/100",
        "originators": ["Die Linke"],
        "type": "Beschlussempfehlung und Bericht",
        "pdf": "u",
    }
    assert items["S"]["aktuelle_stunde"] == ["AfD"] and items["S"]["originators"] == ["AfD"]
    assert formal["by_originator"] == {"Die Linke": 1, "Bundesregierung": 1, "AfD": 1}
    u = t1[0]["unprompted"]
    assert u["weeks"] == ["W0", "W7"] and u["n"] == 2
    assert u["by_fraction"] == {"Die Linke": {"n": 2, "of": 2}}
    assert [s["id"] for s in u["speeches"]] == ["m1", "m2"] and u["speeches"][0]["week"] == "W4"
    # theme 0 is active from week 0: its lead-up is outside the data, so no unprompted measure
    t0 = out["themes"][0]["episodes"]
    assert t0[0]["period_start"] is True and "unprompted" not in t0[0]


def test_main_theme_ties_and_missing_dip_tables(conn):
    rows = [("W", _speech("a", "X")), ("W", _speech("b", "X")), ("W", _speech("c", "Y"))]
    assert origin.main_themes(rows, {"W": {"a": 2, "b": 1, "c": -1}}) == {"X": 1, "Y": -1}
    assert origin.initiators(conn, ["21/1"]) == {}  # an old store without drucksache


def test_index_page_carries_origins(tmp_path):
    from landscape import build

    period = {"themes": [{"id": 0, "terms": ["miete"], "n": 1, "agenda": []}], "assign": {"2026-W28": {"S": 0}}}
    (tmp_path / "period.json").write_text(json.dumps(period))
    page = build.index_page(tmp_path)
    assert '"origins"' not in page  # optional: the index renders without it
    (tmp_path / "topic_origins.json").write_text(json.dumps({"params": {}, "weeks": [], "themes": []}))
    assert '"origins": {"params"' in build.index_page(tmp_path)
