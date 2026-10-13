"""Stable themes: a frozen theme registry (`themes.json`) and the weekly „Thema der Woche“.

`period.py` re-fits UMAP + HDBSCAN whenever the speeches change and numbers its themes by size, so a theme's id,
label and colour change with every weekly rebuild. Here the themes are fitted rarely (`radar themes fit`) and kept:
each has a permanent id ("T007"), a centroid in e5 space and its c-TF-IDF terms. Every week the new documents of
three sources (speeches, hib items, titles of new Drucksachen) are assigned to the nearest centroid by cosine, with
a floor below which a document stays unassigned (`radar build themen`). A refit maps its clusters to the old ids by
Hungarian matching on the centroids, so an id survives as long as its theme does; ids are never reused.

Genre: a hib news item, a Drucksache title and a plenary speech on the same subject are further apart in e5 than two
speeches on different subjects, because e5 also encodes register. So each source is centred on its own mean vector
(stored with the registry) before any cosine is taken; that removes most of the register and leaves the subject.
See radar-lab/report-themen.md for the measurements behind this and the other choices."""

from __future__ import annotations

import datetime as dt
import html
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from radar import corpus, embed, period
from radar.build import RESEARCH
from radar.corpus import has_table, week_id, week_range
from radar.woche import drucksache_pdf, research_path

SOURCES = ("reden", "hib", "drucksachen")
SOURCE_LABEL = {"reden": "Reden", "hib": "hib", "drucksachen": "Drucksachen"}
# Drucksache types whose title names no single subject: bundles of written questions
SKIP_DRUCKSACHE_TYPES = ("Schriftliche Fragen", "Fragen", "Plenarprotokoll")
VERSION = 1  # of the themes.json format


@dataclass
class Doc:
    """One document of any source, as the embedding cache (`embed.embed_speeches`) and the assignment see it."""

    id: str
    source: str
    date: str
    title: str
    text: str  # what is embedded
    url: str
    weight: float = 1.0  # speeches count by words, hib items and Drucksachen by one
    extra: dict = field(default_factory=dict)

    @property
    def week(self) -> str:
        return week_id(self.date)


# --- documents ---------------------------------------------------------------------------------------------------


def speech_url(speech_id: str) -> str:
    """Research's page of a speech: `reden/<rede id>.html`, a later part of a rede as an anchor on it."""
    base = speech_id.split("-")[0]
    return f"{RESEARCH}reden/{base}.html" + (f"#{speech_id}" if speech_id != base else "")


def speeches(conn: sqlite3.Connection, week_ids: list[str]) -> list[Doc]:
    """The speeches of the period model (`period.load`: no Regierungsbefragung, no Zwischenfragen, no Fragestunde)."""
    return [
        Doc(
            id=s.id,
            source="reden",
            date=s.date,
            title=s.agenda_title,
            text=s.text,
            url=speech_url(s.id),
            weight=float(len(s.text.split())),
            extra={"speaker": s.speaker, "fraction": s.fraction},
        )  # fmt: skip
        for _, s in period.load(conn, week_ids)
    ]


def hib(conn: sqlite3.Connection, start: str = "0000", end: str = "9999") -> list[Doc]:
    """hib items between two dates, embedded as title + full text (protected text: analysed, never shown)."""
    if not has_table(conn, "hib_item"):
        return []
    rows = conn.execute(
        "SELECT id, number, date, title, ressort, kind, text, source_url FROM hib_item WHERE date BETWEEN ? AND ? "
        "ORDER BY date, CAST(id AS INTEGER)",
        (start, end),
    )
    return [
        Doc(
            id=f"hib-{r['id']}",
            source="hib",
            date=r["date"],
            title=r["title"],
            text=f"{r['title']}\n\n{r['text']}",
            url=r["source_url"],
            extra={"number": r["number"], "ressort": r["ressort"], "kind": r["kind"]},
        )  # fmt: skip
        for r in rows
    ]


def drucksachen(conn: sqlite3.Connection, start: str = "0000", end: str = "9999") -> list[Doc]:
    """Bundestag Drucksachen of the Wahlperiode between two dates, embedded by title; linked to their Vorgang's
    Research page where DIP has one, else to the PDF."""
    if not has_table(conn, "drucksache"):
        return []
    skip = ",".join("?" * len(SKIP_DRUCKSACHE_TYPES))
    vorgang = has_table(conn, "vorgang_drucksache") and has_table(conn, "vorgang")
    v = (
        "(SELECT min(vd.vorgang_id) FROM vorgang_drucksache vd WHERE vd.drucksache_id = d.id) v, "
        "(SELECT vg.type FROM vorgang vg WHERE vg.id = (SELECT min(vd.vorgang_id) FROM vorgang_drucksache vd "
        "WHERE vd.drucksache_id = d.id)) vt"
        if vorgang
        else "NULL v, NULL vt"
    )
    rows = conn.execute(
        f"SELECT d.id, d.number, d.type, d.title, d.date, {v} FROM drucksache d "
        f"WHERE coalesce(d.publisher, 'BT') = 'BT' AND d.wahlperiode = 21 AND d.date BETWEEN ? AND ? "
        f"AND coalesce(d.type, '') NOT IN ({skip}) ORDER BY d.date, d.number",
        (start, end, *SKIP_DRUCKSACHE_TYPES),
    )
    return [
        Doc(
            id=f"drs-{r['number']}",
            source="drucksachen",
            date=r["date"],
            title=r["title"],
            text=r["title"],
            url=RESEARCH + research_path(r["v"], r["vt"]) if r["v"] else _pdf(r["number"]),
            extra={"number": r["number"], "type": r["type"]},
        )  # fmt: skip
        for r in rows
    ]


def _pdf(number: str) -> str:
    """The PDF of a Drucksache; DIP has a few numbers with stray characters ("21/8057.")."""
    wp, _, n = number.partition("/")
    digits = "".join(ch for ch in n if ch.isdigit())
    return drucksache_pdf(f"{wp}/{digits}") if wp.isdigit() and digits else ""


def vectors(docs: list[Doc], store: sqlite3.Connection, embed_fn=None) -> np.ndarray:
    """Unit e5 vectors, from the cache keyed by text hash (the speeches' cache: same model, chunking and key)."""
    if not docs:
        return np.zeros((0, 768), dtype=np.float32)
    return (embed_fn or embed.embed_speeches)(docs, store)


def embed_all(conn: sqlite3.Connection, store: sqlite3.Connection) -> None:
    """Fill the cache with every hib item and Drucksache title of the Wahlperiode (once ~35 min on CPU, then only
    the new ones). Resumable: the cache is committed every `embed.BATCH` documents."""
    for docs in (hib(conn), drucksachen(conn)):
        print(f"{docs[0].source if docs else '-'}: {len(docs)} documents")
        vectors(docs, store)


# --- the registry: fit and refit ---------------------------------------------------------------------------------

MIN_CLUSTER_SIZE = 60  # as period.py on WP 21; see docs/decisions.md
FLOOR_QUANTILE = 0.05  # a document joins a theme at ≥ the 5th percentile of member-to-own-centroid cosines
MATCH_COS = 0.90  # refit: an old and a new theme are the same at this centroid cosine (source-centred space) …
MATCH_JACCARD = 0.4  # … and this overlap of the member documents both fits share
ID_FORMAT = "T{:03d}"


def unit(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.where(n == 0, 1, n)


def centre(v: np.ndarray, mean: np.ndarray | list[float]) -> np.ndarray:
    """Source-centred unit vectors: the source's mean vector (its register) subtracted, then re-normalised."""
    return unit(v - np.asarray(mean, dtype=np.float32))


def fit_labels(x: np.ndarray, min_cluster_size: int = MIN_CLUSTER_SIZE, seed: int = 42) -> np.ndarray:
    """period.py's clustering: UMAP 5-d (cosine, min_dist 0) + HDBSCAN leaf, noise re-assigned by its neighbours."""
    import umap
    from sklearn.cluster import HDBSCAN

    if len(x) < 2 * min_cluster_size:
        return np.full(len(x), -1)
    low = umap.UMAP(
        n_components=5, n_neighbors=period.N_NEIGHBOURS, min_dist=0.0, metric="cosine", random_state=seed
    ).fit_transform(x)
    labels = HDBSCAN(min_cluster_size=min_cluster_size, cluster_selection_method="leaf").fit_predict(low)
    return period.assign_noise(low, labels)


def centroids(x: np.ndarray, labels: np.ndarray, ids: list[int]) -> np.ndarray:
    return unit(np.vstack([x[labels == c].mean(axis=0) for c in ids]))


def nearest(x: np.ndarray, cents: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Index of the nearest centroid and its cosine for each row (unit vectors)."""
    if not len(cents) or not len(x):
        return np.full(len(x), -1), np.zeros(len(x))
    sims = x @ cents.T
    best = sims.argmax(axis=1)
    return best, sims[np.arange(len(x)), best]


def assign(x: np.ndarray, cents: np.ndarray, floor: float) -> tuple[np.ndarray, np.ndarray]:
    """Theme index per document, -1 below the floor; and the cosine to the nearest centroid."""
    best, cos = nearest(x, cents)
    return np.where(cos >= floor, best, -1), cos


def floors(
    xs: dict[str, np.ndarray], idx: dict[str, np.ndarray], cents: np.ndarray, q: float = FLOOR_QUANTILE
) -> dict[str, float]:
    """The cosine floor per source. `idx[s]` is the theme index of each of source s's fit documents (-1: none).

    A clustered source: the q-quantile of its members' cosines to their own centroid. A source outside the
    clustering (Drucksache titles) has no members to calibrate on; its floor leaves the same share of its period
    documents unassigned as the clustered sources' floors leave of theirs."""
    out: dict[str, float] = {}
    left = []
    for s, x in xs.items():
        member = idx[s] >= 0
        if member.any():
            out[s] = float(np.quantile((x[member] * cents[idx[s][member]]).sum(axis=1), q))
            left.append(float((nearest(x, cents)[1] < out[s]).mean()))
    share = float(np.mean(left)) if left else q
    for s, x in xs.items():
        if s not in out and len(x):
            out[s] = float(np.quantile(nearest(x, cents)[1], share))
    return out


def jaccard(a: set[str], b: set[str], common: set[str]) -> float:
    a, b = a & common, b & common
    return len(a & b) / len(a | b) if a | b else 0.0


def match(
    old_cents: np.ndarray,
    new_cents: np.ndarray,
    old_members: list[set[str]],
    new_members: list[set[str]],
    common: set[str],
    min_cos: float = MATCH_COS,
    min_jaccard: float = MATCH_JACCARD,
) -> dict[int, int]:
    """Hungarian matching of old to new themes on centroid cosine; a pair counts only at ≥ min_cos and a member
    overlap ≥ min_jaccard (over the documents both fits contain). Returns {new index: old index}."""
    from scipy.optimize import linear_sum_assignment

    if not len(old_cents) or not len(new_cents):
        return {}
    sims = new_cents @ old_cents.T
    rows, cols = linear_sum_assignment(-sims)
    return {
        int(r): int(c)
        for r, c in zip(rows, cols, strict=True)
        if sims[r, c] >= min_cos and jaccard(new_members[r], old_members[c], common) >= min_jaccard
    }  # fmt: skip


def fit(
    docs: list[Doc],
    vecs: np.ndarray,
    cluster_sources: tuple[str, ...] = ("reden", "hib"),
    min_cluster_size: int = MIN_CLUSTER_SIZE,
    seed: int = 42,
    labeller=None,
) -> dict:
    """A fresh registry (no ids yet: `register` gives them): centroids, terms, members and floors.

    Every source is centred on its own mean; the documents of `cluster_sources` are clustered together, the others
    only calibrate their floor."""
    from radar.cluster import cluster_terms

    src = np.array([d.source for d in docs])
    means = {s: vecs[src == s].mean(axis=0) for s in dict.fromkeys(src.tolist())}
    x = np.empty_like(vecs)
    for s, m in means.items():
        x[src == s] = centre(vecs[src == s], m)
    inc = np.isin(src, cluster_sources)
    labels = np.full(len(docs), -1)
    labels[inc] = (labeller or fit_labels)(x[inc], min_cluster_size, seed)
    ids = sorted(np.unique(labels[labels >= 0]).tolist(), key=lambda c: -(labels == c).sum())  # biggest first
    rank = {c: k for k, c in enumerate(ids)}
    idx = np.array([rank.get(int(c), -1) for c in labels])
    cents = centroids(x, labels, ids) if ids else np.zeros((0, x.shape[1]), dtype=np.float32)
    fl = floors({s: x[src == s] for s in means}, {s: idx[src == s] for s in means}, cents)
    texts = [d.text for d in docs]
    terms = cluster_terms([texts[i] for i in np.flatnonzero(inc)], labels[inc], top=6) if ids else {}
    themes = []
    for k, c in enumerate(ids):
        member = np.flatnonzero(labels == c)
        by_src = Counter(src[member].tolist())
        sims = x[member] @ cents[k]
        central = [docs[member[j]] for j in np.argsort(-sims)]
        examples = {s: list(dict.fromkeys(d.title for d in central if d.source == s))[:5] for s in by_src}
        themes.append({
            "terms": terms.get(c, []), "n": {s: by_src[s] for s in cluster_sources}, "examples": examples,
            "centroid": cents[k], "members": sorted(docs[i].id for i in member),
        })  # fmt: skip
    return {
        "params": {"min_cluster_size": min_cluster_size, "seed": seed, "cluster_sources": list(cluster_sources),
                   "floor_quantile": FLOOR_QUANTILE, "n_neighbors": period.N_NEIGHBOURS, "model": embed.MODEL},
        "means": means, "floors": fl, "themes": themes,
        "n_docs": dict(Counter(src.tolist())), "unassigned_fit": int((labels[inc] < 0).sum()),
    }  # fmt: skip


def register(new: dict, old: dict | None = None, today: str | None = None, vecs_of=None) -> dict:
    """The registry after a fit: ids carried over from `old` where a new theme matches an old one, else new ids.

    `vecs_of(ids) -> source-centred unit vectors` lets the old themes' centroids be recomputed from their members in
    the new fit's space (the source means move a little between fits); without it the stored centroids are used.
    Unmatched old themes are retired, with the new theme they are closest to as `merged_into` when that is close."""
    today = today or dt.date.today().isoformat()
    version = (old or {}).get("version", 0) + 1
    next_id = (old or {}).get("next_id", 1)
    old_active = [t for t in (old or {}).get("themes", []) if not t.get("retired")]
    new_cents = np.vstack([t["centroid"] for t in new["themes"]]) if new["themes"] else np.zeros((0, 768))
    old_members = [set(t.get("members", [])) for t in old_active]
    if old_active:
        old_cents = np.vstack([
            unit(vecs_of(sorted(m)).mean(axis=0)) if vecs_of and m else np.asarray(t["centroid"], dtype=np.float32)
            for t, m in zip(old_active, old_members, strict=True)
        ])  # fmt: skip
    else:
        old_cents = np.zeros((0, new_cents.shape[1]))
    new_members = [set(t["members"]) for t in new["themes"]]
    common = set().union(*old_members) & set().union(*new_members) if old_members and new_members else set()
    pairs = match(old_cents, new_cents, old_members, new_members, common)
    themes = []
    for k, t in enumerate(new["themes"]):
        if k in pairs:
            prev = old_active[pairs[k]]
            entry = {**{f: prev[f] for f in ("id", "born") if f in prev}, **{f: prev[f] for f in ("label", "label_by")
                                                                            if f in prev}}  # fmt: skip
        else:
            entry = {"id": ID_FORMAT.format(next_id), "born": today}
            next_id += 1
        themes.append({**entry, **{f: t[f] for f in ("terms", "n", "examples", "members")},
                       "centroid": [round(float(v), 5) for v in t["centroid"]]})  # fmt: skip
    matched_old = set(pairs.values())
    retired = []
    for j, t in enumerate(old_active):
        if j in matched_old:
            continue
        sims = new_cents @ old_cents[j] if len(new_cents) else np.zeros(0)
        into = themes[int(sims.argmax())]["id"] if len(sims) and sims.max() >= MATCH_COS - 0.05 else None
        retired.append({**t, "retired": today, **({"merged_into": into} if into else {})})
    already = [t for t in (old or {}).get("themes", []) if t.get("retired")]
    history = (old or {}).get("history", []) + [{
        "version": version, "date": today, "themes": len(themes), "kept": len(pairs),
        "born": len(themes) - len(pairs), "retired": len(retired),
    }]  # fmt: skip
    return {
        "format": VERSION, "version": version, "fitted": today, "params": new["params"], "n_docs": new["n_docs"],
        "means": {s: [round(float(v), 6) for v in m] for s, m in new["means"].items()},
        "floors": {s: round(f, 4) for s, f in new["floors"].items()},
        "next_id": next_id, "themes": themes + already + retired, "history": history,
    }  # fmt: skip


def active(registry: dict) -> list[dict]:
    return [t for t in registry["themes"] if not t.get("retired")]


def theme_centroids(registry: dict) -> np.ndarray:
    return unit(np.asarray([t["centroid"] for t in active(registry)], dtype=np.float32))


def label(theme: dict, n: int = 3) -> str:
    """The generated German name when there is one, else the top c-TF-IDF terms."""
    from radar.build import THEME_ACRONYMS, topic_label

    return theme.get("label") or topic_label(theme["terms"], n, THEME_ACRONYMS)


# --- the week: assignment, z-scores, Thema der Woche --------------------------------------------------------------

BASELINE_WEEKS = 8  # trailing sitting weeks
Z_RISING = 2.0
MIN_DOCS = {"reden": 5, "hib": 3, "drucksachen": 3}  # a theme rises in a source only with this many documents
TOP_DOCS = 4  # strongest documents listed per theme and source
NEW_MIN_CLUSTER = 5  # HDBSCAN size for the week's unassigned pool


def zscore(p: float, base: list[float], n: int) -> tuple[float, float, float]:
    """(z, mean, spread) of this week's share p against the baseline shares. The spread has a binomial floor
    √(μ(1−μ)/n) for n documents this week, with μ at least 1/n, so a small or new theme cannot explode."""
    mu = float(np.mean(base)) if base else 0.0
    sd = float(np.std(base)) if len(base) > 1 else 0.0
    m = max(mu, 1 / n) if n else mu
    floor = (m * (1 - m) / n) ** 0.5 if n else 0.0
    spread = max(sd, floor)
    return ((p - mu) / spread if spread > 0 else 0.0), mu, spread


def shares(idx: np.ndarray, weights: np.ndarray, n_themes: int) -> np.ndarray:
    """Each theme's share of the week's total weight (unassigned documents count in the total)."""
    total = weights.sum()
    out = np.zeros(n_themes)
    if total > 0:
        np.add.at(out, idx[idx >= 0], weights[idx >= 0])
        out /= total
    return out


def week_docs(conn: sqlite3.Connection, source: str, week: str) -> list[Doc]:
    start, end = week_range(week)
    if source == "reden":
        return speeches(conn, [week])
    return hib(conn, start, end) if source == "hib" else drucksachen(conn, start, end)


def doc_entry(d: Doc, cos: float) -> dict:
    e = {"id": d.id, "title": d.title, "date": d.date, "url": d.url, "cos": round(float(cos), 3)}
    if d.source == "reden":
        e.update(speaker=d.extra["speaker"], fraction=d.extra["fraction"])
    elif d.source == "hib":
        e.update(number=d.extra["number"], kind=d.extra["kind"])
    else:
        e.update(number=d.extra["number"], type=d.extra["type"])
    return e


def new_topics(docs: list[Doc], x: np.ndarray, cents: np.ndarray, floor: float, week: str) -> list[dict]:
    """Clusters among the week's unassigned documents (all sources) whose centroid is below the floor to every
    registry theme: candidates for a new theme at the next refit, with a provisional id."""
    from sklearn.cluster import HDBSCAN

    from radar.build import THEME_ACRONYMS, topic_label
    from radar.cluster import cluster_terms

    if len(docs) < 2 * NEW_MIN_CLUSTER:
        return []
    labels = HDBSCAN(min_cluster_size=NEW_MIN_CLUSTER).fit_predict(x)
    ids = [c for c in np.unique(labels[labels >= 0]).tolist()]
    terms = cluster_terms([d.text for d in docs], labels, top=6) if ids else {}
    out = []
    for c in sorted(ids, key=lambda c: -(labels == c).sum()):
        member = np.flatnonzero(labels == c)
        cent = unit(x[member].mean(axis=0))
        near, cos = nearest(cent[None, :], cents)
        if cos[0] >= floor:
            continue
        sims = x[member] @ cent
        out.append({
            "id": f"N-{week}-{len(out) + 1}", "terms": terms.get(c, []),
            "label": topic_label(terms.get(c, []), 3, THEME_ACRONYMS), "n": len(member),
            "sources": dict(Counter(docs[i].source for i in member)), "nearest_cos": round(float(cos[0]), 3),
            "docs": [{**doc_entry(docs[member[j]], sims[j]), "source": docs[member[j]].source}
                     for j in np.argsort(-sims)[:6]],
        })  # fmt: skip
    return out


def week_payload(conn: sqlite3.Connection, store: sqlite3.Connection, registry: dict, week: str, embed_fn=None) -> dict:
    """Per theme and source: this week's share against the trailing sitting weeks, z, whether it rises, the
    strongest documents; the Themen der Woche (rising in ≥ 2 of 3 sources) and the new candidates."""
    from radar.period import area

    sitting = [w["week"] for w in corpus.weeks(conn)]
    baseline = [w for w in sitting if w < week][-BASELINE_WEEKS:]
    themes = active(registry)
    cents = theme_centroids(registry)
    dim = cents.shape[1]
    stats: dict[str, dict] = {}
    week_docs_all, week_x, week_cos = {}, {}, {}
    for source in SOURCES:
        if source not in registry["means"]:
            continue
        per_week = {}
        for w in [*baseline, week]:
            docs = week_docs(conn, source, w)
            x = centre(vectors(docs, store, embed_fn), registry["means"][source]) if docs else np.zeros((0, dim))
            idx, cos = assign(x, cents, registry["floors"][source])
            weights = np.array([d.weight for d in docs])
            per_week[w] = (shares(idx, weights, len(themes)) if docs else np.zeros(len(themes)), len(docs), idx)
            if w == week:
                week_docs_all[source], week_x[source], week_cos[source] = docs, x, (idx, cos)
        stats[source] = per_week
    out_themes = []
    for k, t in enumerate(themes):
        entry = {"id": t["id"], "label": label(t), "terms": t["terms"], "area": area(t["terms"]), "sources": {},
                 **({"label_by": t["label_by"]} if t.get("label_by") else {})}  # fmt: skip
        for source, per_week in stats.items():
            p, n, idx = per_week[week]
            count = int((idx == k).sum())
            base = [float(per_week[w][0][k]) for w in baseline if per_week[w][1]]
            z, mu, spread = zscore(float(p[k]), base, n)
            docs = week_docs_all[source]
            sel = np.flatnonzero(week_cos[source][0] == k)
            top = sel[np.argsort(-week_cos[source][1][sel])][:TOP_DOCS]
            entry["sources"][source] = {
                "share": round(float(p[k]), 4), "mean": round(mu, 4), "spread": round(spread, 4),
                "z": round(z, 2), "n": count, "rising": bool(z >= Z_RISING and count >= MIN_DOCS[source]),
                "baseline": [round(b, 4) for b in base],
                "docs": [doc_entry(docs[i], week_cos[source][1][i]) for i in top],
            }  # fmt: skip
        entry["rising_in"] = [s for s, v in entry["sources"].items() if v["rising"]]
        entry["thema_der_woche"] = len(entry["rising_in"]) >= 2
        out_themes.append(entry)
    out_themes.sort(key=lambda e: (-len(e["rising_in"]), -max((v["z"] for v in e["sources"].values()), default=0)))
    pool = [(s, i) for s in week_docs_all for i in np.flatnonzero(week_cos[s][0] < 0)]
    new = new_topics(
        [week_docs_all[s][i] for s, i in pool],
        np.vstack([week_x[s][i] for s, i in pool]) if pool else np.zeros((0, dim)),
        cents, min(registry["floors"].values()), week,
    )  # fmt: skip
    start, end = week_range(week)
    return {
        "week": week, "start": start, "end": end, "baseline_weeks": baseline,
        "registry": {"version": registry["version"], "fitted": registry["fitted"], "themes": len(themes)},
        "params": {"z_rising": Z_RISING, "min_docs": MIN_DOCS, "baseline": BASELINE_WEEKS,
                   "floors": registry["floors"]},
        "sources": {s: {"docs": len(week_docs_all[s]), "assigned": int((week_cos[s][0] >= 0).sum())}
                    for s in week_docs_all},
        "themen_der_woche": [e["id"] for e in out_themes if e["thema_der_woche"]],
        "themes": out_themes, "neu": new,
    }  # fmt: skip


# --- commands -----------------------------------------------------------------------------------------------------


def load_registry(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save_registry(path: Path, registry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def period_docs(conn: sqlite3.Connection, until: str | None = None) -> list[Doc]:
    """Every document a fit can use: the period's speeches, hib items and Drucksachen, up to `until`."""
    weeks = [w["week"] for w in corpus.weeks(conn)]
    end = until or "9999"
    return [d for d in speeches(conn, weeks) + hib(conn) + drucksachen(conn) if d.date <= end]


def run_fit(
    conn,
    store,
    registry_path: Path,
    refit: bool = False,
    until: str | None = None,
    subset: float = 1.0,
    seed: int = 42,
    min_cluster_size: int | None = None,
) -> dict:
    """Fit the themes and write the registry; with `refit`, ids of the existing registry carry over."""
    docs = period_docs(conn)
    vecs = vectors(docs, store)
    rng = np.random.default_rng(seed)
    keep = np.array([d.date <= (until or "9999") for d in docs]) & (rng.random(len(docs)) < subset)
    fit_docs = [d for d, k in zip(docs, keep, strict=True) if k]
    print(f"fit on {Counter(d.source for d in fit_docs)}")
    new = fit(fit_docs, vecs[keep], min_cluster_size=min_cluster_size or MIN_CLUSTER_SIZE, seed=seed)
    old = load_registry(registry_path) if refit else None
    pos = {d.id: i for i, d in enumerate(docs)}
    src = {d.id: d.source for d in docs}

    def vecs_of(ids: list[str]) -> np.ndarray:
        rows = [i for i in ids if i in pos and src[i] in new["means"]]
        return np.vstack([centre(vecs[pos[i]], new["means"][src[i]]) for i in rows]) if rows else np.zeros((1, 768))

    registry = register(new, old, vecs_of=vecs_of)
    registry["params"].update(until=until, subset=subset)
    save_registry(registry_path, registry)
    h = registry["history"][-1]
    print(f"registry v{registry['version']}: {h['themes']} themes, {h['kept']} kept, {h['born']} new, "
          f"{h['retired']} retired; floors {registry['floors']}")  # fmt: skip
    for t in active(registry):
        print(f"  {t['id']} {t['n']} {', '.join(t['terms'])}")
    return registry


LABEL_PROMPT = """Du benennst ein Thema aus dem Deutschen Bundestag für eine Übersichtsseite.
Gegeben sind die häufigsten Schlagwörter des Themas und Titel typischer Dokumente.
Antworte nur mit einem kurzen deutschen Themennamen aus zwei bis fünf Wörtern, sachlich und neutral,
ohne Anführungszeichen, ohne Wertung, ohne Parteinamen.

Schlagwörter: {terms}
Titel von Tagesordnungspunkten: {reden}
Titel von hib-Meldungen: {hib}"""


def claude_label(theme: dict) -> str:
    import subprocess
    import tempfile

    ex = theme.get("examples", {})
    prompt = LABEL_PROMPT.format(
        terms=", ".join(theme["terms"]),
        reden=" | ".join(ex.get("reden", [])[:5]),
        hib=" | ".join(ex.get("hib", [])[:5]),
    )
    args = ["claude", "-p", "--model", "sonnet", "--tools", "", "--no-session-persistence", prompt]
    # outside the repo, so no project CLAUDE.md is read into the prompt
    out = subprocess.run(
        args, capture_output=True, text=True, timeout=180, check=True, cwd=tempfile.gettempdir()
    ).stdout
    return out.strip().splitlines()[0].strip(" „“\"'.") if out.strip() else ""


def run_label(registry_path: Path, labeller=claude_label) -> int:
    """A generated German name for every active theme that has none yet; generated once, kept across refits."""
    registry = load_registry(registry_path)
    if registry is None:
        raise SystemExit(f"no registry at {registry_path}; run `radar themes fit` first")
    n = 0
    for t in active(registry):
        if t.get("label"):
            continue
        name = labeller(t)
        if name and len(name) <= 60:
            t["label"], t["label_by"] = name, f"generated {dt.date.today().isoformat()}"
            n += 1
            print(f"  {t['id']} {name}  ({', '.join(t['terms'][:4])})")
            save_registry(registry_path, registry)  # after each call: an interrupted run keeps what it has
    return n


def command(args, conn) -> None:
    store = embed.open_store(args.store)
    if args.action == "embed":
        embed_all(conn, store)
    elif args.action == "fit":
        run_fit(conn, store, args.registry, args.refit, args.until, args.subset, args.seed, args.min_cluster_size)
    else:
        print(f"{run_label(args.registry)} labels generated")


def build_command(args, conn) -> None:
    from radar.woche import latest_week

    registry = load_registry(args.registry)
    if registry is None:
        raise SystemExit(f"no registry at {args.registry}; run `radar themes fit` first")
    week = args.week or latest_week(conn)
    payload = week_payload(conn, embed.open_store(args.store), registry, week)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "themen.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "lab-themen.html").write_text(lab_page(payload), encoding="utf-8")
    print(f"themen.json {week}: {len(payload['themen_der_woche'])} Themen der Woche, "
          f"{sum(bool(t['rising_in']) for t in payload['themes'])} rising somewhere, "
          f"{len(payload['neu'])} new")  # fmt: skip
    for t in payload["themes"]:
        if t["rising_in"]:
            zs = " ".join(f"{s}:{v['z']:+.1f}/{v['n']}" for s, v in t["sources"].items())
            print(f"  {'*' if t['thema_der_woche'] else ' '} {t['id']} {t['label']:40} {zs}")
    for n in payload["neu"]:
        print(f"  neu {n['id']} ({n['n']}, {n['sources']}) {n['label']}")


# --- rendering ----------------------------------------------------------------------------------------------------

VIOLET = "#7c3aed"
GREY = "#9ca3af"
SOURCE_NAME = {"reden": "Reden", "hib": "hib", "drucksachen": "Drucksachen"}
METHOD = (
    "Die Themen hat ein Verfahren aus den Texten der Wahlperiode gebildet: Reden und hib-Meldungen als "
    "Sprachmodell-Vektoren (multilingual-e5), gruppiert nach Ähnlichkeit. Sie bleiben fest und behalten ihre Nummer, "
    "auch wenn sie neu berechnet werden. Jedes neue Dokument gehört zu dem Thema, dessen Mitte es am nächsten liegt, "
    "wenn es ihr nahe genug liegt; sonst bleibt es ohne Thema. Die Zuordnung ist automatisch und im Einzelfall falsch. "
    "Ein Thema steigt in einer Quelle, wenn sein Anteil mindestens zwei Standardabweichungen über dem Mittel der acht "
    "Sitzungswochen davor liegt, bei mindestens fünf Reden oder drei Meldungen oder Drucksachen. Reden zählen nach "
    "Wörtern, hib-Meldungen und Drucksachen nach Stück. Die Zahlen messen Aufmerksamkeit, nicht Bedeutung."
)

_CSS = """
.thw{--v:#7c3aed;--vs:#f1ebff;--g:#9ca3af;--ink:#111827;--mute:#6b7280;font:15px/1.45 Inter,system-ui,sans-serif;
  color:var(--ink);max-width:760px}
.thw h2{font-size:1.25rem;margin:0 0 .3rem}.thw h3{font-size:1.02rem;margin:1.4rem 0 .4rem}
.thw .tag{display:inline-block;font-size:.72rem;font-weight:600;color:var(--v);background:var(--vs);border-radius:4px;
  padding:0 .35rem;margin-right:.35rem;vertical-align:.1em}
.thw .intro,.thw .note{color:var(--mute);font-size:.9rem;margin:.2rem 0 .8rem}
.thw .card{border:1px solid #e5e7eb;border-left:3px solid var(--v);border-radius:6px;padding:.7rem .8rem;
  margin:.6rem 0}
.thw .card h4{margin:0;font-size:1rem}.thw .terms{color:var(--mute);font-size:.8rem;margin:.1rem 0 .4rem}
.thw .gen{font-size:.72rem;color:var(--mute);border:1px solid #d1d5db;border-radius:4px;padding:0 .3rem;
  margin-left:.3rem;font-weight:400}
.thw .row{display:grid;grid-template-columns:6.2rem 1fr 7.6rem;align-items:center;gap:.4rem;font-size:.82rem}
.thw .row svg{width:100%;height:22px;display:block}.thw .val{font-variant-numeric:tabular-nums;color:var(--mute)}
.thw .val b{color:var(--ink);font-weight:600}.thw .src{color:var(--ink)}.thw .src.up{font-weight:600}
.thw details{margin-top:.4rem;font-size:.85rem}.thw summary{cursor:pointer;color:var(--v)}
.thw ul{margin:.3rem 0 .2rem;padding-left:1.1rem}.thw li{margin:.15rem 0}.thw a{color:#1d4ed8}
.thw .legend{font-size:.78rem;color:var(--mute);display:flex;gap:.9rem;flex-wrap:wrap;margin:.2rem 0 .4rem}
.thw .legend svg{vertical-align:-2px}
.thw .one li{margin:.25rem 0}
@media (max-width:480px){.thw .row{grid-template-columns:5.2rem 1fr 6.6rem;font-size:.78rem}}
"""


def pct(x: float) -> str:
    return f"{100 * x:.1f}".replace(".", ",") + " %"


def esc(s) -> str:
    return html.escape(str(s))


def strip(v: dict, xmax: float, weeks: list[str]) -> str:
    """One source's row: the baseline weeks as grey dots, the usual range as a grey band, this week as a violet
    dot (filled when the theme rises in this source). Hover titles carry the numbers."""
    w, h, pad = 200, 22, 6

    def x(s: float) -> float:
        return pad + (w - 2 * pad) * min(s / xmax, 1) if xmax else pad

    lo, hi = max(v["mean"] - v["spread"], 0), v["mean"] + v["spread"]
    parts = [
        f'<line x1="{pad}" x2="{w - pad}" y1="{h / 2}" y2="{h / 2}" stroke="#e5e7eb" stroke-width="1"/>',
        f'<rect x="{x(lo):.1f}" y="{h / 2 - 4}" width="{max(x(hi) - x(lo), 1):.1f}" height="8" rx="4" fill="#e5e7eb">'
        f"<title>üblicher Bereich: {pct(lo)} bis {pct(hi)}</title></rect>",
    ]
    for wk, b in zip(weeks[-len(v["baseline"]) :], v["baseline"], strict=False):
        parts.append(f'<circle cx="{x(b):.1f}" cy="{h / 2}" r="3" fill="{GREY}"><title>{wk}: {pct(b)}</title></circle>')
    fill = VIOLET if v["rising"] else "#fff"
    parts.append(
        f'<circle cx="{x(v["share"]):.1f}" cy="{h / 2}" r="5.5" fill="{fill}" stroke="{VIOLET}" stroke-width="2">'
        f"<title>diese Woche: {pct(v['share'])}, {v['n']} Dokumente, z = {v['z']:+.1f}</title></circle>"
    )
    return f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img">{"".join(parts)}</svg>'


def doc_line(source: str, d: dict) -> str:
    if source == "reden":
        text = f"{esc(d['speaker'])} ({esc(d['fraction'])}): {esc(d['title'])}"
    elif source == "hib":
        text = f"hib {esc(d['number'])}: {esc(d['title'])}"
    else:
        text = f"Drs. {esc(d['number'])} ({esc(d['type'])}): {esc(d['title'])}"
    return f'<li><a href="{esc(d["url"])}">{text}</a></li>'


def card(t: dict, weeks: list[str]) -> str:
    xmax = 1.1 * max(max([v["share"], v["mean"] + v["spread"], *v["baseline"]]) for v in t["sources"].values())
    rows = []
    for s, v in t["sources"].items():
        rows.append(
            f'<div class="row"><span class="src{" up" if v["rising"] else ""}">{SOURCE_NAME[s]}</span>'
            f"{strip(v, xmax, weeks)}"
            f'<span class="val"><b>{pct(v["share"])}</b> · sonst {pct(v["mean"])}</span></div>'
        )
    docs = "".join(
        f"<li><b>{SOURCE_NAME[s]}</b><ul>{''.join(doc_line(s, d) for d in v['docs'][:3])}</ul></li>"
        for s, v in t["sources"].items()
        if v["docs"]
    )
    gen = '<span class="gen">Name automatisch erstellt</span>' if t.get("label_by") else ""
    return (
        f'<div class="card" id="thema-{esc(t["id"])}"><h4>{esc(t["label"])}{gen}</h4>'
        f'<div class="terms">{esc(" · ".join(t["terms"]))}</div>{"".join(rows)}'
        f"<details><summary>Dokumente dieser Woche</summary><ul>{docs}</ul></details></div>"
    )


def one_line(t: dict) -> str:
    s = t["rising_in"][0]
    v = t["sources"][s]
    gen = '<span class="gen">Name automatisch erstellt</span>' if t.get("label_by") else ""
    links = ", ".join(f'<a href="{esc(d["url"])}">{esc(d["title"][:90])}</a>' for d in v["docs"][:2])
    return (
        f"<li><b>{esc(t['label'])}</b>{gen} steigt bei {SOURCE_NAME[s]} ({pct(v['share'])}, sonst {pct(v['mean'])}): "
        f"{links}</li>"
    )


def render_section(payload: dict) -> str:
    """The section for the start page: Themen der Woche as cards with the three sources against their baseline,
    themes rising in one source as a list, and the new candidates. Plain HTML + inline SVG, no script."""
    weeks = payload["baseline_weeks"]
    top = [t for t in payload["themes"] if t["thema_der_woche"]]
    one = [t for t in payload["themes"] if len(t["rising_in"]) == 1]
    legend = (
        '<div class="legend"><span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="#7c3aed"/></svg>'
        ' diese Woche, steigt</span><span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="#fff" '
        'stroke="#7c3aed" stroke-width="2"/></svg> diese Woche</span><span><svg width="10" height="10"><circle cx="5" '
        'cy="5" r="3" fill="#9ca3af"/></svg> die acht Sitzungswochen davor</span><span><svg width="18" height="10">'
        '<rect x="0" y="1" width="18" height="8" rx="4" fill="#e5e7eb"/></svg> üblicher Bereich</span></div>'
    )
    out = [
        f'<section class="thw"><style>{_CSS}</style>',
        f'<h2><span class="tag">Radar</span>Themen der Woche {esc(payload["week"].split("-W")[1])}/'
        f"{esc(payload['week'][:4])}</h2>",
        '<p class="intro">Themen, die in dieser Woche mehr Raum bekamen als in den acht Sitzungswochen davor. Gezählt '
        "wird in drei Quellen: Reden im Plenum, Meldungen von „heute im bundestag“ (hib) und neue Drucksachen. Ein "
        "Thema der Woche steigt in mindestens zwei der drei Quellen.</p>",
        legend,
    ]
    out += [card(t, weeks) for t in top] or ['<p class="note">Diese Woche steigt kein Thema in zwei Quellen.</p>']
    if one:
        out.append(f'<h3>Steigt in einer Quelle</h3><ul class="one">{"".join(one_line(t) for t in one)}</ul>')
    if payload["neu"]:
        items = "".join(
            f"<li><b>{esc(n['label'])}</b> ({n['n']} Dokumente): "
            + ", ".join(f'<a href="{esc(d["url"])}">{esc(d["title"][:90])}</a>' for d in n["docs"][:3])
            + "</li>"
            for n in payload["neu"]
        )
        out.append(
            '<h3>Neu diese Woche</h3><p class="note">Gruppen ähnlicher Dokumente dieser Woche, die zu keinem '
            f'bisherigen Thema passen.</p><ul class="one">{items}</ul>'
        )
    out.append(f'<p class="note">{esc(METHOD)}</p></section>')
    return "".join(out)


def lab_page(payload: dict) -> str:
    """A standalone preview of the section."""
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Themen der Woche {esc(payload['week'])} (Labor)</title>"
        '<link rel="stylesheet" href="https://plenar-radar.de/assets/shell.css">'
        "<style>body{margin:0;padding:1rem;background:#fff}</style></head>"
        f"<body>{render_section(payload)}</body></html>"
    )
