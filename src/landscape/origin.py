"""Wer brachte ein Thema auf? Episodes of each period theme and three separate views on how each one started.

An episode is a run of sitting weeks in which a theme is active (its share of the week's words at least
`ACTIVE_SHARE`) after at least `GAP` inactive sitting weeks. For each episode start two measures are kept apart,
never merged into a ranking:

- formal initiative: who put the Vorlagen on the agenda in the first week (initiators of the Vorgang behind each
  Drucksache of the theme's agenda items, the requesting fractions of an Aktuelle Stunde);
- unprompted mentions: speeches on the theme in the `LOOKBACK` sitting weeks before, held in agenda items whose
  main theme is another one, by fraction.

Episodes that start in the first `GAP` weeks of the period have no observable lead-up and are marked as such."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from landscape import period
from landscape.corpus import Speech

ACTIVE_SHARE = 0.04  # a theme is active in a week when it has ≥ 4 % of the week's words, see docs/decisions.md
GAP = 4  # inactive sitting weeks before a new episode
LOOKBACK = 8  # sitting weeks before an episode searched for unprompted mentions
MAX_SPEECHES = 12  # speeches listed per episode (all are counted)

# DIP urheber / Aktuelle-Stunde wording -> the fraction names of `speech.fraction`
FRACTIONS = {
    "cdu/csu": "CDU/CSU", "spd": "SPD", "afd": "AfD", "bündnis 90/die grünen": "BÜNDNIS 90/DIE GRÜNEN",
    "die linke": "Die Linke",
}  # fmt: skip
_VERLANGEN = re.compile(r"auf Verlangen der (.+)", re.IGNORECASE)


def fraction_name(originator: str) -> str:
    """ "Fraktion der AfD" -> "AfD", "Fraktion DIE LINKE" -> "Die Linke"; other originators unchanged."""
    name = re.sub(r"^Fraktion(en)?\s+(der\s+)?", "", originator.strip())
    return FRACTIONS.get(name.lower(), originator.strip())


def aktuelle_stunde(title: str | None) -> list[str] | None:
    """The requesting fractions of an "Aktuelle Stunde | auf Verlangen der Fraktion(en) …" agenda item, else None."""
    if not title or not title.startswith("Aktuelle Stunde"):
        return None
    for segment in title.split("|"):
        if m := _VERLANGEN.search(segment.strip()):
            names = re.sub(r"^Fraktion(en)?\s+(der\s+)?", "", m.group(1).strip())
            return [fraction_name(n.removeprefix("der ").strip()) for n in re.split(r",\s*|\s+und\s+", names) if n]
    return []


def episodes(shares: list[float], threshold: float = ACTIVE_SHARE, gap: int = GAP) -> list[dict]:
    """Runs of active weeks (share ≥ threshold) separated by ≥ gap inactive weeks; shorter dips stay inside the
    episode. `start`/`end` are week indices (end = last active week); `period_start` when fewer than `gap` weeks
    lie before the first episode, so its lead-up is not in the data."""
    out: list[dict] = []
    last = None
    for i, s in enumerate(shares):
        if s < threshold:
            continue
        if last is not None and i - last - 1 < gap:
            out[-1]["end"] = i
        else:
            out.append({"start": i, "end": i, "period_start": last is None and i < gap})
        last = i
    return out


def weekly_shares(rows: list[tuple[str, Speech]], assign: dict, week_ids: list[str], theme_ids: list[int]) -> dict:
    """theme id -> share of each sitting week's words (all included speeches of the week are the denominator)."""
    words = defaultdict(Counter)
    total = Counter()
    for week, s in rows:
        n = len(s.text.split())
        total[week] += n
        words[assign.get(week, {}).get(s.id, -1)][week] += n
    return {t: [words[t][w] / total[w] if total[w] else 0.0 for w in week_ids] for t in theme_ids}


def main_themes(rows: list[tuple[str, Speech]], assign: dict) -> dict[str, int]:
    """agenda item id -> the theme most of its speeches have (ties: the lower theme id); -1 if none has one."""
    count: dict[str, Counter] = defaultdict(Counter)
    for week, s in rows:
        if s.agenda_item_id:
            count[s.agenda_item_id][assign.get(week, {}).get(s.id, -1)] += 1
    return {a: min(c.items(), key=lambda kv: (-kv[1], kv[0]))[0] for a, c in count.items()}


def initiators(conn, numbers: list[str]) -> dict[str, dict]:
    """Drucksache number -> {originators, type, pdf}: the initiators of the Vorgänge the Drucksache belongs to (so
    a Beschlussempfehlung counts for whoever tabled the Antrag), else the Drucksache's own urheber. Empty when the
    store has no DIP tables."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not numbers or "drucksache" not in tables:
        return {}
    marks = ",".join("?" * len(numbers))
    out: dict[str, dict] = {}
    for r in conn.execute(
        f"SELECT id, number, type, originators, pdf_url FROM drucksache WHERE number IN ({marks})", numbers
    ):
        via = []
        if "vorgang" in tables:
            for (ini,) in conn.execute(
                "SELECT v.initiators FROM vorgang v JOIN vorgang_drucksache vd ON vd.vorgang_id = v.id "
                "WHERE vd.drucksache_id = ?",
                (r[0],),
            ):
                via += json.loads(ini)
        names = list(dict.fromkeys(fraction_name(o) for o in (via or json.loads(r[3]))))
        out[r[1]] = {"originators": names, "type": r[2], "pdf": r[4]}
    return out


def formal(conn, items: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Formal initiative for the agenda items of an episode's first week: per item the Vorlagen with their
    initiators and, for an Aktuelle Stunde, the requesting fractions; plus the number of items per originator."""
    numbers = sorted({n for it in items for n in it["drucksachen"]})
    ds = initiators(conn, numbers)
    out, count = [], Counter()
    for it in items:
        vorlagen = [{"number": n, **ds[n]} for n in it["drucksachen"] if n in ds]
        requested = aktuelle_stunde(it["raw_title"])
        names = list(dict.fromkeys((requested or []) + [o for v in vorlagen for o in v["originators"]]))
        count.update(names)
        out.append(
            {
                "agenda_id": it["id"],
                "title": it["title"],
                "date": it["date"],
                "originators": names,
                **({"aktuelle_stunde": requested} if requested is not None else {}),
                "vorlagen": vorlagen,
            }  # fmt: skip
        )
    return out, dict(count.most_common())


def agenda_items(conn, ids: list[str]) -> dict[str, dict]:
    """agenda item id -> raw title and Drucksache numbers from the foundation store."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {
        r[0]: {"raw_title": r[1], "drucksachen": json.loads(r[2] or "[]")}
        for r in conn.execute(f"SELECT id, title, drucksache_numbers FROM agenda_item WHERE id IN ({marks})", ids)
    }


def origins(conn, rows: list[tuple[str, Speech]], model: dict, week_ids: list[str]) -> dict:
    """topic_origins.json: per theme its episodes with both measures, plus the parameters."""
    assign = model["assign"]
    theme_ids = [t["id"] for t in model["themes"]]
    shares = weekly_shares(rows, assign, week_ids, theme_ids)
    main = main_themes(rows, assign)
    raw = agenda_items(conn, sorted(main))
    by_week: dict[str, list[tuple[Speech, int]]] = defaultdict(list)
    for week, s in rows:
        by_week[week].append((s, assign.get(week, {}).get(s.id, -1)))
    themes = []
    for tid in theme_ids:
        eps = []
        for ep in episodes(shares[tid]):
            first = week_ids[ep["start"]]
            items = {}
            for s, _ in by_week[first]:
                if s.agenda_item_id and main.get(s.agenda_item_id) == tid and s.agenda_item_id not in items:
                    r = raw.get(s.agenda_item_id, {"raw_title": None, "drucksachen": s.drucksachen})
                    items[s.agenda_item_id] = {"id": s.agenda_item_id, "title": s.agenda_title, "date": s.date, **r}
            formal_items, formal_count = formal(conn, list(items.values()))
            entry = {
                "start": first, "end": week_ids[ep["end"]], "period_start": ep["period_start"],
                "share": round(shares[tid][ep["start"]], 3),
                "formal": {"items": formal_items, "by_originator": formal_count},
            }  # fmt: skip
            if not ep["period_start"]:
                entry["unprompted"] = unprompted(
                    week_ids[max(0, ep["start"] - LOOKBACK) : ep["start"]], by_week, main, tid
                )
            eps.append(entry)
        themes.append({"id": tid, "episodes": eps})
    params = {"active_share": ACTIVE_SHARE, "gap": GAP, "lookback": LOOKBACK}
    return {"params": params, "weeks": week_ids, "themes": themes}


def unprompted(weeks: list[str], by_week: dict, main: dict[str, int], tid: int) -> dict:
    """Speeches on theme `tid` in `weeks`, held in agenda items whose main theme is another one: count per fraction
    next to the fraction's speeches in those weeks, and the first `MAX_SPEECHES` for reading."""
    count, total, speeches = Counter(), Counter(), []
    for week in weeks:
        for s, theme in by_week.get(week, []):
            total[s.fraction] += 1
            if theme == tid and main.get(s.agenda_item_id, -1) != tid:
                count[s.fraction] += 1
                speeches.append({"id": s.id, "week": week, "date": s.date, "speaker": s.speaker,
                                 "fraction": s.fraction, "agenda": s.agenda_title})  # fmt: skip
    return {
        "weeks": [weeks[0], weeks[-1]] if weeks else [],
        "by_fraction": {f: {"n": n, "of": total[f]} for f, n in count.most_common()},
        "n": sum(count.values()),
        "speeches": speeches[:MAX_SPEECHES],
    }


def build(conn, week_ids: list[str], model: dict) -> dict:
    """Origins from the foundation store and the period model (`period.json`)."""
    return origins(conn, period.load(conn, week_ids), model, week_ids)
