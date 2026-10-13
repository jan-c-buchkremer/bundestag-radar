import json

import numpy as np
import pytest
from conftest import HIB

from radar import themes


def blobs(n_per: int, centres: np.ndarray, seed: int = 0, noise: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = np.vstack([c + noise * rng.standard_normal((n_per, len(c))) for c in centres])
    return themes.unit(x), np.repeat(np.arange(len(centres)), n_per)


def docs_for(labels: np.ndarray, prefix: str = "d", source: str = "reden") -> list[themes.Doc]:
    return [
        themes.Doc(id=f"{prefix}{i}", source=source, date="2026-09-08", title=f"t{lab}",
                   text=f"wort{'abcdef'[lab]} thema{'abcdef'[lab]}", url="u")
        for i, lab in enumerate(labels)
    ]  # fmt: skip


def test_assign_uses_nearest_centroid_and_floor():
    cents = themes.unit(np.array([[1.0, 0, 0], [0, 1.0, 0]]))
    x = themes.unit(np.array([[0.9, 0.1, 0], [0.1, 1.0, 0], [0, 0, 1.0], [0.5, 0.5, 0.7]]))
    idx, cos = themes.assign(x, cents, floor=0.6)
    assert idx.tolist() == [0, 1, -1, -1]
    assert cos[0] > 0.99 and cos[2] == pytest.approx(0)


def test_centre_removes_the_source_register():
    # two sources on the same two subjects, each shifted by its own register direction
    subj = np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])
    a = themes.unit(subj + [0, 0, 3.0, 0])
    b = themes.unit(subj + [0, 0, 0, 3.0])
    assert (a[0] @ b[0]) < (a[0] @ a[1])  # raw: register beats subject
    ca, cb = themes.centre(a, a.mean(axis=0)), themes.centre(b, b.mean(axis=0))
    assert (ca[0] @ cb[0]) > 0.99 and (ca[0] @ ca[1]) < 0  # centred: subject wins


def test_zscore_has_a_binomial_floor():
    z, mu, spread = themes.zscore(0.10, [0.02, 0.02, 0.02, 0.02], n=100)  # flat baseline: sd 0
    assert mu == pytest.approx(0.02)
    assert spread == pytest.approx((0.02 * 0.98 / 100) ** 0.5)
    assert z == pytest.approx(0.08 / spread)
    z, _, spread = themes.zscore(0.03, [0.0] * 8, n=100)  # a new theme: μ is taken as at least 1/n
    assert spread == pytest.approx((0.01 * 0.99 / 100) ** 0.5) and z == pytest.approx(0.03 / spread)
    z, _, spread = themes.zscore(0.3, [0.1, 0.3, 0.2, 0.4], n=1000)  # a noisy baseline: its own sd
    assert spread == pytest.approx(np.std([0.1, 0.3, 0.2, 0.4])) and z < 1


def test_shares_count_unassigned_in_the_total():
    s = themes.shares(np.array([0, 0, 1, -1]), np.array([10.0, 30.0, 20.0, 40.0]), 3)
    assert s.tolist() == pytest.approx([0.4, 0.2, 0.0])


def fake_fit(x: np.ndarray, labels: np.ndarray, prefix: str) -> dict:
    """What `fit` returns, with the clustering replaced by known labels."""
    docs = docs_for(labels, prefix)
    return themes.fit(docs, x, cluster_sources=("reden",), min_cluster_size=2, labeller=lambda *_: labels)


def test_refit_keeps_ids_by_hungarian_matching():
    rng = np.random.default_rng(1)
    c = themes.unit(rng.standard_normal((4, 16)))
    x, lab = blobs(30, c[:3])
    old = themes.register(fake_fit(x, lab, "d"), today="2026-10-01")
    ids_old = {tuple(sorted(t["members"]))[:1]: t["id"] for t in themes.active(old)}
    assert sorted(ids_old.values()) == ["T001", "T002", "T003"]
    # the refit sees theme 0 and 1 again (other order, other noise, same documents), theme 2 is gone, 3 is new
    x2, lab2 = blobs(30, c[[1, 3, 0]], seed=5)
    members = {1: [f"d{i}" for i in range(30, 60)], 0: [f"d{i}" for i in range(30)]}
    new = fake_fit(x2, lab2, "n")
    for t in new["themes"]:  # give the re-found themes their old documents, as a refit on the same corpus would
        which = int(np.argmax(c[[1, 3, 0]] @ np.asarray(t["centroid"])))
        t["members"] = members.get([1, 3, 0][which], t["members"])

    def vecs_of(ids):  # the old members in the new fit's centred space, as run_fit passes them
        return themes.centre(x[[int(i[1:]) for i in ids]], new["means"]["reden"])

    reg = themes.register(new, old, today="2026-12-01", vecs_of=vecs_of)
    act = {t["id"]: t for t in themes.active(reg)}
    retired = [t for t in reg["themes"] if t.get("retired")]
    by_old = {t["id"]: set(t["members"]) for t in themes.active(old)}
    for tid in act:
        if tid in by_old:
            assert act[tid]["members"] and set(act[tid]["members"]) == by_old[tid]
    assert len(act) == 3 and len(retired) == 1
    assert {"T004"} == set(act) - set(by_old)  # the new theme gets the next id, never a reused one
    assert retired[0]["id"] not in act and reg["next_id"] == 5
    assert reg["history"][-1] == {"version": 2, "date": "2026-12-01", "themes": 3, "kept": 2, "born": 1, "retired": 1}


def test_match_needs_cosine_and_member_overlap():
    a = themes.unit(np.array([[1.0, 0], [0, 1.0]]))
    m = [{"x1", "x2"}, {"y1", "y2"}]
    common = {"x1", "x2", "y1", "y2"}
    assert themes.match(a, a[::-1], m, m[::-1], common) == {0: 1, 1: 0}
    assert themes.match(a, a, m, [{"y1"}, {"x1"}], common) == {}  # same centroids, but other documents


def test_floors_per_source():
    rng = np.random.default_rng(2)
    c = themes.unit(rng.standard_normal((2, 8)))
    x, lab = blobs(50, c, noise=0.1)
    cents = themes.centroids(x, lab, [0, 1])
    out = themes.floors({"reden": x, "drucksachen": x[:20]}, {"reden": lab, "drucksachen": np.full(20, -1)}, cents)
    own = (x * cents[lab]).sum(axis=1)
    assert out["reden"] == pytest.approx(np.quantile(own, 0.05))
    assert 0 < out["drucksachen"] < 1


def test_week_payload_and_section(conn):
    """Two themes (housing, budget) in a 4-d toy space; the speeches of the fixture embedded by keyword."""
    conn.executescript(HIB)
    conn.executemany(
        "INSERT INTO hib_item (id, number, date, wahlperiode, title, ressort, kind, text, source_url) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(str(i), f"{i}/2026", "2026-09-08", 21, "Mieten steigen", "Bau", "Antwort", "Wohnen Mieten",
          f"https://hib/{i}") for i in range(4)],
    )  # fmt: skip

    def embed_fn(docs, _store):
        return themes.unit(np.array([[1.0, 0.1, 0, 0] if "Wohnen" in d.text else [0.1, 1.0, 0, 0] for d in docs]))

    reg = {
        "version": 1, "fitted": "2026-10-01", "next_id": 3,
        "means": {"reden": [0, 0, 0, 0], "hib": [0, 0, 0, 0]}, "floors": {"reden": 0.5, "hib": 0.5},
        "themes": [{"id": "T001", "terms": ["wohnen", "mieten"], "centroid": [1, 0, 0, 0]},
                   {"id": "T002", "terms": ["haushalt"], "centroid": [0, 1, 0, 0]}],
    }  # fmt: skip
    p = themes.week_payload(conn, None, reg, "2026-W37", embed_fn=embed_fn)
    assert p["baseline_weeks"] == ["2026-W28"]
    t1 = next(t for t in p["themes"] if t["id"] == "T001")
    assert t1["sources"]["hib"]["n"] == 4 and t1["sources"]["hib"]["share"] == 1.0
    assert t1["sources"]["hib"]["docs"][0]["url"].startswith("https://hib/")
    assert t1["sources"]["hib"]["rising"]  # 4 items, none in the baseline week
    assert not t1["sources"]["reden"]["rising"]  # one speech is below MIN_DOCS
    json.dumps(p)
    html = themes.render_section(p)
    assert "Themen der Woche 37/2026" in html and "<script" not in html
    p["themes"][0]["thema_der_woche"] = True
    p["themes"][0]["label_by"] = "generated 2026-10-12"
    assert 'class="card"' in themes.render_section(p) and "automatisch erstellt" in themes.render_section(p)


def test_speech_url():
    assert themes.speech_url("ID219915100").endswith("reden/ID219915100.html")
    assert themes.speech_url("ID21800600-2").endswith("reden/ID21800600.html#ID21800600-2")
