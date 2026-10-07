"""„Diese Woche“: what "heute im bundestag" (hib) reported in the newest week, for the top of the index page.

hib is the Bundestag's news service: short items on Vorlagen, answers and committee sessions. The foundation stores
them (`hib_item`, `hib_drucksache`); their texts are protected (foundation docs/licences.md), so this module never
reads `text`: the page shows title, date, issue number, Ressort, kind, committee and the link to the article, and
connects each item through its Drucksachen to the Vorgang and to the agenda items where the plenum took them up.

The selection is hib's, the grouping by Ressort is ours: Radar, with a method note on the page."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from radar.corpus import has_table, week_id, week_range

FILE = "woche.json"  # in the output directory, next to period.json; build.index_page reads it

METHOD = (
    "„Diese Woche“ zeigt die Meldungen von „heute im bundestag“ (hib) der neuesten Woche, nach Ressort und Art "
    "gruppiert. Welche Vorgänge eine Meldung bekommen, entscheidet die hib-Redaktion; die Zahlen zählen Meldungen, "
    "nicht Bedeutung. Über die Drucksachennummern führt jede Meldung zu ihrem Vorgang und zur Tagesordnung."
)


def drucksache_pdf(number: str) -> str:
    """The Bundestag's PDF of a Drucksache from its number: 21/8309 -> …/btd/21/083/2108309.pdf (Research's
    `data.drucksache_pdf`)."""
    wp, n = number.split("/")
    n5 = f"{int(n):05d}"
    return f"https://dserver.bundestag.de/btd/{wp}/{n5[:3]}/{wp}{n5}.pdf"


def latest_week(conn: sqlite3.Connection, today: str | None = None) -> str | None:
    """The ISO week of the newest hib item up to `today`; None without hib in the store."""
    if not has_table(conn, "hib_item"):
        return None
    today = today or dt.date.today().isoformat()
    row = conn.execute("SELECT max(date) FROM hib_item WHERE date <= ?", (today,)).fetchone()
    return week_id(row[0]) if row and row[0] else None


def _drucksachen(conn: sqlite3.Connection, numbers: list[str]) -> dict[str, dict]:
    """Per Drucksache number: type, title, PDF, Vorgang id (when the store has DIP) and the agenda items that list
    it, oldest first."""
    out: dict[str, dict] = {n: {"number": n, "pdf": drucksache_pdf(n), "plenum": []} for n in numbers}
    if not numbers:
        return out
    marks = ",".join("?" * len(numbers))
    if has_table(conn, "drucksache"):
        vorgang = has_table(conn, "vorgang_drucksache")
        # hib links Bundestag Drucksachen; a Bundesrat one ("21/26") can share the number
        bt = "AND coalesce(d.publisher, 'BT') = 'BT'" if has_table(conn, "drucksache", "publisher") else ""
        rows = conn.execute(
            f"SELECT d.number, d.type, d.title, d.pdf_url, "
            f"{'(SELECT min(vorgang_id) FROM vorgang_drucksache WHERE drucksache_id = d.id)' if vorgang else 'NULL'} v "
            f"FROM drucksache d WHERE d.number IN ({marks}) {bt} ORDER BY d.date",
            numbers,
        )
        for r in rows:
            out[r["number"]].update(
                {"type": r["type"], "title": r["title"], "pdf": r["pdf_url"] or out[r["number"]]["pdf"],
                 **({"vorgang": r["v"]} if r["v"] else {})}
            )  # fmt: skip
    for r in conn.execute(
        f"SELECT DISTINCT j.value n, a.id, a.top_id, st.date FROM agenda_item a, json_each(a.drucksache_numbers) j "
        f"JOIN sitting st ON st.id = a.sitting_id WHERE j.value IN ({marks}) ORDER BY st.date, a.id",
        numbers,
    ):
        out[r["n"]]["plenum"].append(
            {"agenda_id": r["id"], "top": r["top_id"], "date": r["date"], "week": week_id(r["date"])}
        )
    return out


def load(conn: sqlite3.Connection, week: str | None = None, today: str | None = None) -> dict | None:
    """The hib items of `week` (default: the newest week with items) with their Drucksachen, newest first; None
    when the store has no hib or the week none. `current` says whether the week is the one `today` falls in."""
    today = today or dt.date.today().isoformat()
    week = week or latest_week(conn, today)
    if week is None or not has_table(conn, "hib_item"):
        return None
    start, end = week_range(week)
    rows = conn.execute(
        "SELECT id, number, date, title, ressort, kind, committee, source_url FROM hib_item "
        "WHERE date BETWEEN ? AND ? ORDER BY date DESC, CAST(id AS INTEGER) DESC",
        (start, end),
    ).fetchall()
    if not rows:
        return None
    links: dict[str, list[str]] = {}
    if has_table(conn, "hib_drucksache"):
        marks = ",".join("?" * len(rows))
        for r in conn.execute(
            f"SELECT hib_id, drucksache_number FROM hib_drucksache WHERE hib_id IN ({marks}) ORDER BY hib_id, position",
            [r["id"] for r in rows],
        ):
            links.setdefault(r["hib_id"], []).append(r["drucksache_number"])
    ds = _drucksachen(conn, sorted({n for ns in links.values() for n in ns}))
    sittings = [
        r[0] for r in conn.execute("SELECT DISTINCT date FROM sitting WHERE date BETWEEN ? AND ? ORDER BY date",
                                   (start, end))
    ]  # fmt: skip
    items = [
        {
            "id": r["id"],
            "number": r["number"],
            "date": r["date"],
            "title": r["title"],
            "ressort": r["ressort"],
            "kind": r["kind"],
            "url": r["source_url"],
            **({"committee": r["committee"]} if r["committee"] else {}),
            "drucksachen": [ds[n] for n in links.get(r["id"], [])],
        }  # fmt: skip
        for r in rows
    ]
    return {"week": week, "start": start, "end": end, "current": week == week_id(today), "sittings": sittings,
            "items": items}  # fmt: skip


def on_maps(week: dict, payloads: dict[str, dict]) -> dict:
    """Marks every agenda item of `week` whose speeches are on a built week map (`map`: the item's link can open
    that map filtered to it)."""
    agendas = {w: {s["agenda_id"] for s in p["speeches"]} for w, p in payloads.items()}
    for item in week["items"]:
        for d in item["drucksachen"]:
            for a in d["plenum"]:
                a["map"] = a["agenda_id"] in agendas.get(a["week"], ())
    return week


def write(out: Path, conn: sqlite3.Connection, today: str | None = None) -> int | None:
    """`woche.json` in `out`; returns the number of items, None (and no file) without hib in the store."""
    path = out / FILE
    data = load(conn, today=today)
    if data is None:
        path.unlink(missing_ok=True)  # an old week must not stay on the page as "Diese Woche"
        return None
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(data["items"])
