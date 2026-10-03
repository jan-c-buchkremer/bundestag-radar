"""Assemble the map data for one week and render the static HTML pages."""

from __future__ import annotations

import html
import json
import os
import shutil
from pathlib import Path

import numpy as np

from landscape.cluster import Clustering, majority, neighbours
from landscape.corpus import Speech

HERE = Path(__file__).parent
# where the MdB cards are published; speaker names and agenda items link there
CARDS = os.environ.get("CARDS_URL", "https://jan-c-buchkremer.github.io/bundestag-mdb-cards/").rstrip("/") + "/"
# upper-cased in topic labels; the same set as `ACRONYMS` in template.html, so exported labels match the week page
ACRONYMS = {
    "usa", "nato", "eu", "uno", "un", "csd", "ard", "zdf", "kfw", "dfb", "fifa", "ki", "eeg", "geg", "stpo", "bgb",
    "sgb", "bka", "bnd", "öpnv", "lng", "co2", "nis", "tv", "gkv", "pkv", "iwf", "oecd", "wto",
}  # fmt: skip
# the index page's list (`ACRONYMS` in index.html), for period theme labels
THEME_ACRONYMS = {
    "usa", "nato", "eu", "uno", "un", "kfw", "ki", "eeg", "geg", "bka", "bnd", "öpnv", "lng", "co2", "gkv", "pkv",
    "iwf", "oecd", "bmz", "kfor", "unifil", "kritis", "spd", "cdu", "csu", "afd", "ard", "zdf",
}  # fmt: skip
# e5 similarities are compressed (nearest neighbours at 0.86–0.97 on WP 21); below this the match is mostly style
# or a short Regierungsbefragung turn, see docs/decisions.md
MIN_SIMILARITY = 0.88


def week_payload(
    week: str,
    speeches: list[Speech],
    clustering: Clustering,
    vectors: np.ndarray,
    weeks: list[str],
    decisions: dict[str, list[dict]] | None = None,
) -> dict:
    agenda_of = majority([s.agenda_title for s in speeches], clustering.labels)
    clusters = [
        {"id": c, "terms": terms, "agenda": agenda_of[c], "n": int((clustering.labels == c).sum())}
        for c, terms in sorted(clustering.terms.items())
    ]
    order = sorted(speeches, key=lambda s: s.start)
    following = {s.id: next((o.id for o in order if o.start > s.end), None) for s in speeches}  # skips Zwischenfragen
    points = [
        {
            "id": s.id,
            "x": round(float(x), 3),
            "y": round(float(y), 3),
            "cluster": int(label),
            "speaker": s.speaker,
            "person_id": s.person_id,
            "photo": s.photo,
            **({"photo_credit": s.photo_credit} if s.photo_credit else {}),
            "fraction": s.fraction,
            "role": s.role,
            "date": s.date,
            "agenda_id": s.agenda_item_id,
            "agenda": s.agenda_title,
            "top": s.top_id,
            "drucksachen": s.drucksachen,
            "chars": len(s.text),
            "comments": s.n_comments,
            "pdf": s.pdf_url,
            "cite": s.source_document_id,
            "similar": [speeches[j].id for j in near],
            "next": following[s.id],
            "paragraphs": s.paragraphs,
            **({"parts": s.part_ids} if len(s.part_ids) > 1 else {}),  # foundation ids re-joined into this speech
            **({"zwischenfrage": True} if s.zwischenfrage else {}),
        }  # fmt: skip
        for s, (x, y), label, near in zip(speeches, clustering.xy, clustering.labels, neighbours(vectors), strict=True)
    ]
    linked = {k: v for s in speeches for k, v in s.linked.items()}  # marker targets too short to be points
    return {
        "week": week, "weeks": weeks, "clusters": clusters, "speeches": points, "linked": linked,
        "decisions": decisions or {},  # agenda item id -> announced results, see corpus.decisions
    }  # fmt: skip


def _inline(template: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", r"<\/")
    page = (HERE / template).read_text(encoding="utf-8").replace("__CARDS__", json.dumps(CARDS))
    page = page.replace("__CARDS_URL__", html.escape(CARDS))  # in attributes: the footer's Impressum/Datenschutz
    return page.replace("__DATA__", data)


def write_assets(out: Path) -> None:
    """Copy assets/ (Plotly, the Inter font) next to the pages, which load them from there instead of a CDN."""
    shutil.copytree(HERE / "assets", out / "assets", dirs_exist_ok=True)


def render(payload: dict) -> str:
    return _inline("template.html", payload)


def render_index(summaries: list[dict], overview: dict | None = None) -> str:
    """Landing page: the period's themes over the weeks (`period.overview`), then one card per week, from
    `summary()` of each week payload."""
    return _inline("index.html", {"weeks": summaries, "period": overview})


def load_payloads(out: Path) -> dict[str, dict]:
    """Every week payload written to `out`, by week id."""
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("20*-W*.json"))}


def index_page(out: Path) -> str:
    """The index from what is in `out`: the week payloads and, if built, the period model (`period.json`)."""
    from landscape import period

    payloads = load_payloads(out)
    cache = out / "period.json"
    overview = period.overview(json.loads(cache.read_text(encoding="utf-8")), payloads) if cache.exists() else None
    if overview and (origins := out / "topic_origins.json").exists():  # origin.py, "Wer brachte das Thema auf?"
        overview["origins"] = json.loads(origins.read_text(encoding="utf-8"))
    return render_index([summary(p) for p in payloads.values()], overview)


def summary(payload: dict) -> dict:
    dates = sorted({s["date"] for s in payload["speeches"]})
    return {
        "week": payload["week"], "dates": dates, "speeches": len(payload["speeches"]),
        "clusters": [c["terms"][:3] for c in sorted(payload["clusters"], key=lambda c: -c["n"])],
    }  # fmt: skip


def topic_label(terms: list[str], n: int = 3, acronyms: set[str] = ACRONYMS) -> str:
    """A week topic's label as the week page shows it: "Miete · Wohnen · KFW"."""
    return " · ".join(t.upper() if t in acronyms else t[:1].upper() + t[1:] for t in terms[:n])


def speech_clusters(payloads: dict[str, dict]) -> dict[str, dict]:
    """Every clustered speech of the built weeks -> its week topic, for sites linking into the maps
    (`<week>.html#cluster=<cluster_id>`). Keyed by foundation speech id, continuation parts (`ID…-3`) included;
    unclustered speeches are left out."""
    out: dict[str, dict] = {}
    for week, p in sorted(payloads.items()):
        label = {c["id"]: topic_label(c["terms"]) for c in p["clusters"]}
        for s in p["speeches"]:
            if s["cluster"] in label:
                entry = {"week": week, "cluster_id": s["cluster"], "label": label[s["cluster"]]}
                for sid in s.get("parts", [s["id"]]):
                    out[sid] = entry
    return out


def _write(path: Path, mapping: dict) -> int:
    path.write_text(json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return len(mapping)


def write_speech_clusters(out: Path) -> int:
    """`speech_clusters.json` from every week payload in `out`; returns the number of speech ids."""
    return _write(out / "speech_clusters.json", speech_clusters(load_payloads(out)))


def speech_themes(period: dict, payloads: dict[str, dict]) -> dict[str, dict]:
    """Every speech with a period theme -> theme id and label as the index page shows them. Keyed like
    `speech_clusters` (continuation parts from the week payloads); speeches outside every theme are left out."""
    label = {t["id"]: topic_label(t["terms"], acronyms=THEME_ACRONYMS) for t in period.get("themes", [])}
    parts = {s["id"]: s.get("parts", [s["id"]]) for p in payloads.values() for s in p["speeches"]}
    out: dict[str, dict] = {}
    for assign in period.get("assign", {}).values():
        for sid, theme in assign.items():
            if theme in label:
                for part in parts.get(sid, [sid]):
                    out[part] = {"theme_id": theme, "label": label[theme]}
    return out


def write_speech_themes(out: Path) -> int | None:
    """`speech_themes.json` from `period.json` and the week payloads in `out`; None when the period is not built."""
    cache = out / "period.json"
    if not cache.exists():
        return None
    return _write(
        out / "speech_themes.json", speech_themes(json.loads(cache.read_text(encoding="utf-8")), load_payloads(out))
    )


def speech_neighbours(
    speeches: list[Speech], vectors: np.ndarray, k: int = 5, min_sim: float = MIN_SIMILARITY
) -> dict[str, list[str]]:
    """Every speech -> the ids of up to k most similar speeches from other agenda items, anywhere in the given
    set (the whole period on `build --all`). Keyed like `speech_clusters`; speeches without a match above
    `min_sim` are left out."""
    near = nearest_speeches(vectors, [s.agenda_item_id or None for s in speeches], k, min_sim)
    return {
        part: [speeches[j].id for j in js] for s, js in zip(speeches, near, strict=True) if js for part in s.part_ids
    }


def write_speech_neighbours(out: Path, speeches: list[Speech], vectors: np.ndarray) -> int:
    """`speech_neighbours.json` over `speeches`; returns the number of speech ids."""
    return _write(out / "speech_neighbours.json", speech_neighbours(speeches, vectors))


def nearest_speeches(
    vectors: np.ndarray, groups: list[str | None], k: int = 5, min_sim: float = 0.0, block: int = 1024
) -> list[list[int]]:
    """For each unit vector the indices of its k most similar others (cosine, best first), skipping those of the
    same group (agenda item; None never matches) and those below `min_sim`. Blockwise, so memory stays at
    block × n similarities."""
    n = len(vectors)
    codes = {g: i for i, g in enumerate(sorted({g for g in groups if g is not None}))}
    # a None group gets a code of its own per speech, so it only excludes the speech itself
    group = np.array([codes[g] if g is not None else len(codes) + i for i, g in enumerate(groups)])
    vectors = np.asarray(vectors, dtype=np.float32)
    k = min(k, n - 1)
    out: list[list[int]] = []
    for lo in range(0, n, block):
        sims = vectors[lo : lo + block] @ vectors.T
        rows = np.arange(len(sims))
        sims[rows, rows + lo] = -np.inf
        sims[group[lo : lo + block, None] == group[None, :]] = -np.inf
        if k <= 0:
            out.extend([] for _ in rows)
            continue
        top = np.argpartition(-sims, k - 1, axis=1)[:, :k]
        top_sims = np.take_along_axis(sims, top, axis=1)
        order = np.argsort(-top_sims, axis=1, kind="stable")
        top, top_sims = np.take_along_axis(top, order, axis=1), np.take_along_axis(top_sims, order, axis=1)
        out.extend(t[s >= min_sim].tolist() for t, s in zip(top, top_sims, strict=True))
    return out
