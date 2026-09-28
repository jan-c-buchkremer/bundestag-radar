from conftest import LONG

from landscape import corpus


def test_weeks_are_iso_weeks(conn):
    w = corpus.weeks(conn)
    assert [x["week"] for x in w] == ["2026-W28", "2026-W37"]
    assert w[0]["sittings"] == [88, 89] and w[0]["speeches"] == 5


def test_week_range():
    assert corpus.week_range("2026-W28") == ("2026-07-06", "2026-07-12")
    assert corpus.week_id("2026-07-10") == "2026-W28"


def test_load_week_rejoins_splits_and_fills_fraction(conn):
    speeches = corpus.load_week(conn, "2026-W28")
    by_id = {s.id: s for s in speeches}
    assert set(by_id) == {"ID0", "ID1", "ID2"}  # ID1-2 is a short question, ID1-3 merged into ID1
    berg = by_id["ID1"]
    assert berg.part_ids == ["ID1", "ID1-3"] and berg.text.endswith("Kurze Antwort.")
    assert [k for k, _ in berg.paragraphs] == ["text", "comment", "chair", "zwischenfrage", "text"]
    assert berg.paragraphs[3] == ("zwischenfrage", "ID1-2") and berg.n_comments == 1
    assert berg.linked["ID1-2"]["paragraphs"] == [("text", "Kurze Frage?"), ("antwort", "ID1")]
    assert berg.start == ("2026-07-08", 2) and berg.end == ("2026-07-08", 4)
    assert berg.speaker == "Bernd Berg"
    assert by_id["ID2"].fraction == "SPD" and by_id["ID2"].role == "Ministerin"  # from person.party
    assert by_id["ID0"].fraction == corpus.NO_FRACTION
    assert by_id["ID1"].agenda_title == "Mietpreisbremse verlängern" and by_id["ID1"].drucksachen == ["21/100"]


def test_short_title():
    st = corpus.short_title
    assert st(None, "Einzelplan 04") == "Haushalt: Bundeskanzler"
    assert st(None, "Einzelplan 14") == "Haushalt: Verteidigung"
    assert st(None, "Einzelplan 99") == "Haushalt: Einzelplan 99"
    assert st("(Schluss: 18:01 Uhr)", "Einzelplan 23") == "Einzelplan 23"
    assert st(None, "Einzelplan") == "Haushalt"
    assert st(None, "Zur Geschäftsordnung") == "Zur Geschäftsordnung"
    assert (
        st("Aktuelle Stunde | auf Verlangen der Fraktion der AfD | Angriffe in Erfurt", "Zusatzpunkt 1")
        == "Angriffe in Erfurt"
    )
    assert st("Vereinbarte Debatte: | 250 Jahre USA", "Tagesordnungspunkt 3") == "250 Jahre USA"
    gesetz = (
        "5 a) Erste Beratung des von der Bundesregierung eingebrachten Entwurfs eines Gesetzes zur Änderung der StPO"
        " | b) Beratung des Antrags"
    )
    assert st(gesetz, "Tagesordnungspunkt 5") == "Zur Änderung der StPO"
    assert st("Befragung der Bundesregierung", "Tagesordnungspunkt 1") == "Befragung der Bundesregierung"
    zp = (
        "ZP 28 a) – Zweite und dritte Beratung des von der Bundesregierung eingebrachten Entwurfs eines Gesetzes zur X"
        " | – zu dem Antrag der Abgeordneten Y"
    )
    assert st(zp, "Zusatzpunkt 28, 29") == "Zur X"


def _interrupted_rede(conn, base, chair, asker_text):
    """Adler speaks, Cohn interrupts after the chair's remark, Adler answers (sitting 21/91, week 2026-W37)."""
    conn.executemany(
        "INSERT INTO speech VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (base, "21/91", "21/91/1", 10, "1", "Anna Adler (SPD)", None, "SPD", LONG, "BT-PlPr. 21/91"),
            (f"{base}-2", "21/91", "21/91/1", 11, "3", "Clara Cohn (Die Linke)", None, "Die Linke", asker_text, "x"),
            (f"{base}-3", "21/91", "21/91/1", 12, "1", "Anna Adler (SPD)", None, "SPD", "Antwort.", "x"),
        ],
    )  # fmt: skip
    conn.executemany(
        "INSERT INTO speech_paragraph VALUES (?,?,?,?,?)",
        [
            (f"{base}/1", base, 1, "text", LONG),
            (f"{base}/2", base, 2, "chair", chair),
            (f"{base}-2/1", f"{base}-2", 1, "text", asker_text),
            (f"{base}-3/1", f"{base}-3", 1, "text", "Antwort."),
        ],
    )


def test_zwischenfrage_flag(conn):
    _interrupted_rede(conn, "ID5", "Gestatten Sie eine Zwischenfrage?", LONG)
    _interrupted_rede(conn, "ID6", "Das Wort zu einer Kurzintervention hat Clara Cohn.", LONG)
    by_id = {s.id: s for s in corpus.load_week(conn, "2026-W37")}
    assert by_id["ID5-2"].zwischenfrage and by_id["ID5-2"].paragraphs[-1] == ("antwort", "ID5")
    assert not by_id["ID6-2"].zwischenfrage  # Kurzinterventionen stay normal points
    assert not by_id["ID5"].zwischenfrage and not by_id["ID3"].zwischenfrage


def test_link_fields_without_new_tables(conn):
    speeches = corpus.load_week(conn, "2026-W28")
    assert all(s.photo and s.photo_credit is None for s in speeches)  # no person_photo: every portrait is tried
    assert corpus.photos(conn) is None
    assert corpus.decisions(conn, ["21/88/2"]) == {}


def test_link_fields_with_new_tables(conn):
    conn.executescript(
        """
        CREATE TABLE person_photo (person_id TEXT PRIMARY KEY, image_url TEXT, credit TEXT, bio_url TEXT);
        INSERT INTO person_photo VALUES ('2', 'https://x/berg.jpg', 'Foto: Achim Melde', NULL);
        CREATE TABLE decision (id TEXT PRIMARY KEY, sitting_id TEXT, agenda_item_id TEXT, n INTEGER, kind TEXT,
            subject TEXT, drucksache_number TEXT, result TEXT, roll_call_vote_id TEXT, text TEXT);
        INSERT INTO decision VALUES ('21/88/h2', '21/88', '21/88/2', 2, 'handzeichen', 'Antrag Mietpreisbremse',
            '21/100', 'abgelehnt', NULL, '...');
        INSERT INTO decision VALUES ('21/88/h1', '21/88', '21/88/2', 1, 'namentlich', 'Beschlussempfehlung', NULL,
            'angenommen', '21/88/1', '...');
        CREATE TABLE roll_call_vote (id TEXT PRIMARY KEY, sitting_id TEXT, number INTEGER, title TEXT, yes INTEGER,
            no INTEGER, agenda_item_id TEXT);
        INSERT INTO roll_call_vote VALUES ('21/88/1', '21/88', 1, 'Beschlussempfehlung', 300, 200, '21/88/2');
        INSERT INTO roll_call_vote VALUES ('21/88/2', '21/88', 2, 'Entschließungsantrag', 100, 400, '21/88/2');
        """
    )
    by_id = {s.id: s for s in corpus.load_week(conn, "2026-W28")}
    assert by_id["ID1"].photo and by_id["ID1"].photo_credit == "Foto: Achim Melde"
    assert not by_id["ID2"].photo
    got = corpus.decisions(conn, ["21/88/2", "21/88/1"])
    assert list(got) == ["21/88/2"]
    assert [(d["page"], d["result"], d["kind"]) for d in got["21/88/2"]] == [
        ("abstimmungen/21-88-1.html", "angenommen", "namentlich"),  # decision with a roll-call vote: its vote page
        ("abstimmungen/21-88-h2.html", "abgelehnt", "handzeichen"),
        ("abstimmungen/21-88-2.html", "abgelehnt", "namentlich"),  # roll-call vote without a decision row
    ]
