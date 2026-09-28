import json

import numpy as np

from landscape import build, corpus, period
from landscape.cluster import Clustering, cluster, cluster_terms, majority, neighbours


def test_cluster_terms_pick_distinctive_words():
    texts = ["wärmepumpe gas heizen kosten"] * 5 + ["kindergeld familien eltern kinder"] * 5
    labels = np.array([0] * 5 + [1] * 5)
    terms = cluster_terms(texts, labels, top=2)
    assert set(terms[0]) <= {"wärmepumpe", "gas", "heizen", "kosten"}
    assert set(terms[1]) <= {"kindergeld", "familien", "eltern", "kinder"}
    assert majority(["a", "a", "b", "c", "c", "c", "x", "x", "x", "x"], labels) == {0: "a", 1: "x"}


def test_neighbours_and_tiny_week_layout():
    v = np.array([[1, 0], [0.9, 0.1], [0, 1]], dtype=float)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    assert neighbours(v, k=1) == [[1], [0], [1]]
    tiny = cluster(v, ["a", "b", "c"], min_cluster_size=8)  # too few speeches for UMAP/HDBSCAN
    assert tiny.xy.shape == (3, 2) and list(tiny.labels) == [-1, -1, -1] and tiny.terms == {}


def test_render_embeds_payload(conn):
    speeches = corpus.load_week(conn, "2026-W28")
    assert [s.id for s in speeches] == ["ID0", "ID1", "ID1-2", "ID2"]
    labels = np.array([0, 0, 0, -1])
    clustering = Clustering(xy=np.zeros((4, 2)), labels=labels, terms={0: ["miete", "wohnen", "kfw"]})
    vectors = np.eye(4)
    payload = build.week_payload("2026-W28", speeches, clustering, vectors, ["2026-W28", "2026-W37"])
    assert payload["clusters"] == [
        {"id": 0, "terms": ["miete", "wohnen", "kfw"], "agenda": "Mietpreisbremse verlängern", "n": 3}
    ]
    p = payload["speeches"][1]
    assert [s["next"] for s in payload["speeches"]] == ["ID1", "ID2", "ID2", None]  # ID1 skips its Zwischenfrage
    assert payload["linked"] == {} and payload["speeches"][2]["zwischenfrage"] is True
    assert p["comments"] == 1 and p["cluster"] == 0 and p["pdf"].endswith("21088.pdf") and len(p["similar"]) == 3
    assert p["parts"] == ["ID1", "ID1-3"] and "parts" not in payload["speeches"][0]
    html = build.render(payload)
    assert "__DATA__" not in html
    start = html.index("const DATA = ") + len("const DATA = ")
    assert json.loads(html[start : html.index(";\n", start)].replace("<\\/", "</")) == json.loads(json.dumps(payload))
    index = build.render_index([build.summary(payload)])
    assert '"speeches": 4' in index and '"2026-07-08"' in index and "#tour=" in index


def test_payload_link_fields(conn):
    speeches = corpus.load_week(conn, "2026-W28")
    clustering = Clustering(xy=np.zeros((4, 2)), labels=np.array([0, 0, 0, -1]), terms={0: ["miete"]})
    decisions = {"21/88/2": [{"id": "21/88/h1", "page": "abstimmungen/21-88-h1.html", "result": "angenommen"}]}
    payload = build.week_payload("2026-W28", speeches, clustering, np.eye(4), ["2026-W28"], decisions)
    p = payload["speeches"][1]
    assert p["person_id"] == "2" and p["photo"] is True and "zwischenfrage" not in p
    assert payload["decisions"] == decisions


def test_speech_clusters_export(conn, tmp_path):
    speeches = corpus.load_week(conn, "2026-W28")
    clustering = Clustering(xy=np.zeros((4, 2)), labels=np.array([3, 3, 3, -1]), terms={3: ["kfw", "miete", "x", "y"]})
    payload = build.week_payload("2026-W28", speeches, clustering, np.eye(4), ["2026-W28"])
    (tmp_path / "2026-W28.json").write_text(json.dumps(payload))
    (tmp_path / "period.json").write_text("{}")  # not a week payload
    assert build.write_speech_clusters(tmp_path) == 4
    got = json.loads((tmp_path / "speech_clusters.json").read_text())
    entry = {"week": "2026-W28", "cluster_id": 3, "label": "KFW · Miete · X"}
    assert got == {"ID0": entry, "ID1": entry, "ID1-3": entry, "ID1-2": entry}  # ID2 is unclustered


def _synthetic_period(n_per=30):
    """Three well-separated topics over two weeks; texts carry a topic word so the labels are checkable."""
    rng = np.random.default_rng(0)
    words = ["wärmepumpe", "kindergeld", "bundeswehr"]
    rows, vecs = [], []
    for k, word in enumerate(words):
        centre = np.zeros(16)
        centre[k] = 1
        for i in range(n_per):
            week = "2026-W28" if i % 3 else "2026-W37"
            s = corpus.Speech(
                id=f"S{k}-{i}", date="2026-07-08", person_id="1", speaker="A", fraction="SPD", role=None, top_id="T",
                agenda_item_id="21/88/2", agenda_title=f"Debatte {word}", drucksachen=[],
                text=f"{word} {word} thema{i} " * 5, pdf_url="", source_document_id="", part_ids=[f"S{k}-{i}"],
            )  # fmt: skip
            rows.append((week, s))
            v = centre + 0.05 * rng.standard_normal(16)
            vecs.append(v / np.linalg.norm(v))
    return rows, np.array(vecs, dtype=np.float32)


def test_period_model_and_overview(conn, tmp_path):
    rows, vecs = _synthetic_period()
    model = period.model(rows, vecs, min_cluster_size=10)
    assert len(model["themes"]) == 3 and sum(t["n"] for t in model["themes"]) == 90
    assert {t["terms"][0] for t in model["themes"]} == {"wärmepumpe", "kindergeld", "bundeswehr"}
    assert set(model["assign"]) == {"2026-W28", "2026-W37"} and len(model["assign"]["2026-W28"]) == 60
    assert model["themes"][0]["agenda"][0].startswith("Debatte ")
    # week maps: in W28 every speech of topic k sits in week cluster 10 + k, W37 is all noise
    theme_of = {sid: t for w in model["assign"].values() for sid, t in w.items()}
    week_payloads = {
        "2026-W28": {"speeches": [{"id": s.id, "cluster": 10 + int(s.id[1])} for w, s in rows if w == "2026-W28"]},
        "2026-W37": {"speeches": [{"id": s.id, "cluster": -1} for w, s in rows if w == "2026-W37"]},
    }
    ov = period.overview(model, week_payloads)
    assert ov["weeks"] == ["2026-W28", "2026-W37"] and ov["unassigned"] == [0, 0]
    for t in ov["themes"]:
        assert t["counts"] == [20, 10] and t["focus"][1] is None and t["area"] in {"klima", "soziales", "aussen"}
        topic = int(next(sid for sid, th in theme_of.items() if th == t["id"])[1])
        assert t["focus"][0] == 10 + topic
    index = build.render_index([], ov)
    assert '"period": {"weeks"' in index and "#cluster=" in index


def test_period_build_is_cached(conn, tmp_path):
    rows = period.load(conn, ["2026-W28", "2026-W37"])
    assert {s.id for _, s in rows} == {"ID1", "ID2", "ID3"}  # Befragung (ID0) left out
    calls = []

    def fake_embed(speeches, store):
        calls.append(len(speeches))
        return np.eye(len(speeches), 4, dtype=np.float32)

    cache = tmp_path / "period.json"
    first = period.build(conn, ["2026-W28", "2026-W37"], None, cache, embed_fn=fake_embed)
    again = period.build(conn, ["2026-W28", "2026-W37"], None, cache, embed_fn=fake_embed)
    assert calls == [3] and first == again and first["themes"] == []  # too few speeches for themes
    assert json.loads(cache.read_text())["assign"]["2026-W37"] == {"ID3": -1}


def test_period_area():
    assert period.area(["bundeswehr", "soldaten", "sicherheit", "nato"]) == "aussen"
    assert period.area(["infrastruktur", "kritis", "bevölkerungsschutz", "sicherheit"]) == "sicherheit"
    assert period.area(["pflege", "versorgung", "patienten"]) == "gesundheit"
    assert period.area(["xylophon", "quark"]) == "weitere"
