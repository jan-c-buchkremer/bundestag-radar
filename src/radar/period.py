"""Themes of the whole Wahlperiode: one clustering over every speech, counted per sitting week.

The model (themes + which speech belongs to which) is cached in `period.json` and recomputed only when the set of
speeches or the parameters change. The overview for the index page joins it with the week payloads, so a theme in
a week points to the week-level cluster that holds most of its speeches there."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np

from radar import corpus
from radar.cluster import cluster_terms
from radar.corpus import Speech

MIN_CLUSTER_SIZE = 60  # WP 21 (8.7 k speeches): 32 themes, see docs/decisions.md
N_NEIGHBOURS = 15
KNN = 15  # HDBSCAN noise joins the majority theme of its KNN nearest clustered speeches …
KNN_AGREE = 0.5  # … if at least this share of them agree

# policy areas for the overview's colours: a theme goes to the area whose stems match its top terms best (rank-weighted)
AREAS = {
    "wirtschaft": (
        "wirtschaft investi haushalt schulden steuer geld industrie unternehmen automobil einkommen "
        "erbschaft vermögen preise tankrabatt mineralöl entlastung verbraucher wachstum tourismus "
        "mittelstand bürokratie"
    ),
    "soziales": (
        "rente altersvorsorge arbeit beschäftigt tarif schwarzarbeit kinder familie eltern kindergeld "
        "wohn bauen mietpreis miete bürgergeld sozial"
    ),
    "sicherheit": (
        "sicherheit migration polizei bundespolizei grenze dobrindt justiz rechtsstaat täter opfer gewalt "
        "kritis kritische bevölkerungsschutz asyl abschiebung straf"
    ),
    "aussen": (
        "europa europäisch iran usa ukraine russland putin krieg frieden bundeswehr soldat wehrpflicht "
        "nato israel libanon sudan hisbollah hamas unifil bosnien kosovo kfor entwicklung humanitär bmz "
        "welt china"
    ),
    "klima": (
        "strom erneuerbar energie kernkraft klima umwelt natur wärme heiz gas landwirt bauern ländlich ernährung"
    ),
    "gesundheit": ("pflege patient gesundheit versorgung kranken krankenhaus"),
    "digital": (
        "digital daten verwaltung staatsmodern bahn schiene infrastruktur mobilität deutschlandticket "
        "verkehr forschung wissenschaft hightech raumfahrt bafög bildung"
    ),
    "demokratie": (
        "demokratie partei linken kultur kunst medien rundfunk plattform queer diskriminierung "
        "grundgesetz frauen sport athlet spiele"
    ),
}
AREA_STEMS = {area: stems.split() for area, stems in AREAS.items()}


def area(terms: list[str]) -> str:
    """The policy area of a theme from its top terms; "weitere" when no stem matches."""
    score = dict.fromkeys(AREA_STEMS, 0)
    for rank, term in enumerate(terms):
        for a, stems in AREA_STEMS.items():
            if any(term.startswith(st) for st in stems):
                score[a] += len(terms) - rank
    best = max(score, key=lambda a: score[a])
    return best if score[best] else "weitere"


def included(s: Speech) -> bool:
    """Regierungsbefragung and Zwischenfragen are turns, not debates; they would blur the themes."""
    return s.agenda_kind != "befragung" and not s.zwischenfrage


def load(conn, week_ids: list[str]) -> list[tuple[str, Speech]]:
    """(week, speech) for every included speech of the period, in speaking order."""
    return [(w, s) for w in week_ids for s in corpus.load_week(conn, w) if included(s)]


def params(min_cluster_size: int = MIN_CLUSTER_SIZE, seed: int = 42) -> dict:
    return {"min_cluster_size": min_cluster_size, "n_neighbors": N_NEIGHBOURS, "knn": KNN, "seed": seed}


def fingerprint(speeches: list[Speech], params: dict) -> str:
    h = hashlib.sha1(json.dumps(params, sort_keys=True).encode())
    for s in speeches:
        h.update(s.id.encode() + b"\0" + hashlib.sha1(s.text.encode()).digest())
    return h.hexdigest()


def assign_noise(low: np.ndarray, labels: np.ndarray, k: int = KNN, agree: float = KNN_AGREE) -> np.ndarray:
    """HDBSCAN leaves a third of the period unclustered; give each such speech the theme most of its nearest
    clustered neighbours (in the clustering space) have, when enough of them agree."""
    from sklearn.neighbors import KNeighborsClassifier

    core = labels >= 0
    if core.sum() < k or core.all():
        return labels
    knn = KNeighborsClassifier(k).fit(low[core], labels[core])
    prob = knn.predict_proba(low[~core])
    out = labels.copy()
    out[~core] = np.where(prob.max(axis=1) >= agree, knn.classes_[prob.argmax(axis=1)], -1)
    return out


def model(
    rows: list[tuple[str, Speech]], vectors: np.ndarray, min_cluster_size: int = MIN_CLUSTER_SIZE, seed: int = 42
) -> dict:
    """Themes of the period: UMAP 5-d + HDBSCAN (leaf) + c-TF-IDF labels, noise re-assigned by nearest neighbours."""
    import umap
    from sklearn.cluster import HDBSCAN

    speeches = [s for _, s in rows]
    if len(rows) < 2 * min_cluster_size:
        labels = np.full(len(rows), -1)
    else:
        low = umap.UMAP(
            n_components=5, n_neighbors=N_NEIGHBOURS, min_dist=0.0, metric="cosine", random_state=seed
        ).fit_transform(vectors)
        labels = HDBSCAN(min_cluster_size=min_cluster_size, cluster_selection_method="leaf").fit_predict(low)
        labels = assign_noise(low, labels)
    terms = cluster_terms([s.text for s in speeches], labels, top=6) if (labels >= 0).any() else {}
    # ids by size, so theme 0 is the biggest
    ranked = sorted(terms, key=lambda c: -(labels == c).sum())
    new_id = {c: i for i, c in enumerate(ranked)}
    themes = []
    for c in ranked:
        members = [s for s, lab in zip(speeches, labels, strict=True) if lab == c]
        agenda = Counter(s.agenda_title for s in members).most_common(3)
        themes.append({"id": new_id[c], "terms": terms[c], "n": len(members), "agenda": [a for a, _ in agenda]})
    assign: dict[str, dict[str, int]] = {}
    for (week, s), lab in zip(rows, labels, strict=True):
        assign.setdefault(week, {})[s.id] = new_id.get(int(lab), -1)
    p = params(min_cluster_size, seed)
    return {"fingerprint": fingerprint(speeches, p), "params": p, "themes": themes, "assign": assign}


def build(
    conn, week_ids: list[str], store, cache: Path, min_cluster_size: int = MIN_CLUSTER_SIZE, embed_fn=None
) -> dict:
    """The period model, from `cache` if the speeches and parameters are unchanged, else computed and cached."""
    from radar import embed

    rows = load(conn, week_ids)
    speeches = [s for _, s in rows]
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("fingerprint") == fingerprint(speeches, params(min_cluster_size)):
            print(f"overview: {len(speeches)} speeches unchanged, themes from {cache}")
            return cached
    print(f"overview: clustering {len(speeches)} speeches (no Regierungsbefragung, no Zwischenfragen)")
    vectors = (embed_fn or embed.embed_speeches)(speeches, store)
    result = model(rows, vectors, min_cluster_size)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def overview(period: dict, week_payloads: dict[str, dict]) -> dict:
    """What the index page draws: per theme and sitting week the speech count and the week-level cluster that
    holds most of those speeches (None when they are all unclustered in that week's map)."""
    weeks = sorted(set(period["assign"]) | set(week_payloads))
    cluster_of = {w: {s["id"]: s["cluster"] for s in p["speeches"]} for w, p in week_payloads.items()}
    themes = []
    for t in period["themes"]:
        counts, focus = [], []
        for w in weeks:
            ids = [i for i, theme in period["assign"].get(w, {}).items() if theme == t["id"]]
            top = Counter(c for i in ids if (c := cluster_of.get(w, {}).get(i, -1)) >= 0).most_common(1)
            counts.append(len(ids))
            focus.append(top[0][0] if top else None)
        themes.append(
            {
                **{k: t[k] for k in ("id", "terms", "n", "agenda")},
                "area": area(t["terms"]),
                "counts": counts,
                "focus": focus,
            }
        )
    unassigned = [sum(v == -1 for v in period["assign"].get(w, {}).values()) for w in weeks]
    return {"weeks": weeks, "themes": themes, "unassigned": unassigned}
