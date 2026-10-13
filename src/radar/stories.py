"""„Geschichten der Woche“: one story per Vorgang, followed across every source the store has for the week.

A reader asks "what happened to X this week", not "which hib items appeared". The foundation already holds the
hard links between the sources (radar-lab research 03 §3): hib items name their Drucksachen (`hib_drucksache`), a
Drucksache belongs to a DIP Vorgang (`vorgang_drucksache`), an agenda item lists its Vorlagen with their Vorgang
(`agenda_item_vorlage`) and carries the speeches, and a decision knows its Vorgang (`decision_vorgang`). So the DIP
Vorgang is the story key, and this module only joins; nothing here is learned or embedded, every link can be named
("über Drucksache 21/8309").

Two joins make one story out of several Vorgänge, because the plenum and hib treat them as one: Vorlagen debated in
the same debated agenda item (a Gesetzentwurf with the Anträge called up with it), and the Vorgänge a hearing
reported by hib was held on (a bill and two Anträge). Other hib items often cite older Drucksachen as context; they
belong to each Vorgang they name but join none. The story is named after its lead Vorgang (a
Gesetzgebung before an Antrag before the rest). Agenda items with many Vorlagen (Überweisungen im vereinfachten
Verfahren, Sammelübersichten) are bundles, not stories, and do not join.

What has no Vorgang stays visible: a hib item whose Drucksache DIP has not delivered yet is a story of its own, a
committee report without any Drucksache joins a story only when its committee is the story's lead committee (or its
hib Ressort the story's) and its title shares enough words with the story; otherwise it stands alone. Debates
without Vorlagen (Aktuelle Stunden, vereinbarte Debatten) and roll calls without a Vorgang are stories too.

The order is transparent and coarse: how many kinds of source carry the story this week (hib, Drucksache, Debatte,
Abstimmung, namentliche Abstimmung), then the number of hib items and of speeches. The page shows the kinds
("Quellen: hib, Debatte, Abstimmung"), never a rank number. hib texts are read only for the committee heuristic;
`stories.json` carries no hib text (foundation docs/licences.md)."""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from radar.corpus import has_table, short_title, week_range
from radar.origin import fraction_name
from radar.woche import AREA_OF_RESSORT, EINBRINGER, drucksache_pdf, einbringer, latest_week, research_path

FILE = "stories.json"
RESEARCH = os.environ.get("RESEARCH_URL", "https://plenar-radar.de/").rstrip("/") + "/"

MAX_AGENDA_VORGAENGE = 12  # a debated agenda item with more Vorgänge is a bundle (Sammelübersichten), no story join
MAX_HIB_VORGAENGE = 4  # a hib item naming more Vorgänge cites context, it does not report on them together
JOINING_KINDS = ("Anhörung",)  # a hearing is held on a bill and the Anträge with it; other items only cite them
MIN_SHARED_WORDS = 2  # committee heuristic: shared title stems (≥ 5 letters) between a loose hib item and a story
OVERVIEW_RESSORTS = ("Bundestagsnachrichten",)  # „Das Parlament“'s weekly overview cites the whole week: no story
TOP_N = 6  # stories the lead text and the "kurz erklärt" are written for

# the kinds of source, in the order the card names them
SIGNALS = ("hib", "drucksache", "debatte", "abstimmung", "namentlich")
SIGNAL_LABEL = {"hib": "hib", "drucksache": "Drucksache", "debatte": "Debatte", "abstimmung": "Abstimmung",
                "namentlich": "namentliche Abstimmung"}  # fmt: skip

# Research's stages of a Gesetzgebung by DIP Beratungsstand (bundestag-research-platform procedures.STAGE, labels
# from procedures.STAGES/ENDED); any other Vorgang shows DIP's Stand as it is
STAGE = {
    "Noch nicht beraten": "Eingebracht",
    "Dem Bundestag zugeleitet - Noch nicht beraten": "Eingebracht",
    "1. Durchgang im Bundesrat abgeschlossen": "Eingebracht",
    "In der Beratung (Einzelheiten siehe Vorgangsablauf)": "In den Ausschüssen",
    "Überwiesen": "In den Ausschüssen",
    "Beschlussempfehlung liegt vor": "In den Ausschüssen",
    "Verabschiedet": "Im Bundestag beschlossen",
    "Bundesrat hat Vermittlungsausschuss nicht angerufen": "Bundesrat",
    "Bundesrat hat zugestimmt": "Bundesrat",
    "Vermittlungsvorschlag liegt vor": "Bundesrat",
    "Verkündet": "Verkündet",
    "Abgelehnt": "Abgelehnt",
    "Für erledigt erklärt": "Für erledigt erklärt",
    "Bundesrat hat Zustimmung versagt": "Zustimmung versagt",
}
TYPE_RANK = {"Gesetzgebung": 0, "Antrag": 1}  # which Vorgang names a story of several

METHOD = (
    "Eine Geschichte ist ein Vorgang des Dokumentations- und Informationssystems (DIP), verfolgt über alle Quellen "
    "der Woche: Meldungen von „heute im bundestag“ (hib), Drucksachen, Tagesordnungspunkte mit ihren Reden und "
    "Abstimmungen. Die Verbindungen stammen aus den Daten selbst: hib nennt die Drucksachen, das DIP ordnet sie dem "
    "Vorgang zu, die Tagesordnung nennt die beratenen Vorlagen. Vorlagen, die in einem Tagesordnungspunkt gemeinsam "
    "beraten wurden oder zu denen eine gemeinsame Anhörung stattfand, bilden eine Geschichte. Ausschussmeldungen "
    "ohne Drucksache kommen nur dazu, wenn Ausschuss und Stichwörter passen. Die Reihenfolge richtet sich nach der "
    "Zahl der Quellenarten (hib, Drucksache, Debatte, Abstimmung, namentliche Abstimmung), dann nach der Zahl der "
    "hib-Meldungen und Reden. Sie zählt, wie viele Quellen eine Geschichte tragen, nicht ihre Bedeutung."
)

_WORD = re.compile(r"[a-zäöüß]{5,}")
_STOP = {"bundesregierung", "fraktion", "antrag", "gesetz", "gesetzes", "entwurf", "entwurfs", "deutschen",
         "bundestag", "bundestages", "ausschuss", "beratung", "unterrichtung", "anfrage", "antwort", "kleine",
         "fragen", "sowie", "zwischen", "werden", "wurde", "durch", "gegen", "unter", "einer", "eines", "dieser",
         "fordert", "fordern", "weitere", "keine", "experten", "sachverständige", "regierung"}  # fmt: skip


def stems(text: str) -> set[str]:
    """Content-word stems of a title for the committee heuristic: lower-case words of ≥ 5 letters, cut to 6."""
    return {w[:6] for w in _WORD.findall(text.lower()) if w not in _STOP}


def stage(vtype: str | None, status: str | None) -> dict | None:
    """The card's stage label: Research's stage for a Gesetzgebung, DIP's Stand for anything else."""
    if not status:
        return None
    label = STAGE.get(status, status) if vtype == "Gesetzgebung" else status
    return {"label": label, "dip": status}


def page_id(any_id: str) -> str:
    return any_id.replace("/", "-")


def decision_url(page: str, vorgaenge: list[str]) -> str:
    """Research's place of a decision (D15): a point on its Vorgang's timeline when it has exactly one, else its own
    page (bundestag-research-platform `data.decision_href`)."""
    if len(vorgaenge) == 1:
        return f"{RESEARCH}vorgaenge/{vorgaenge[0]}.html#abst-{page}"
    return f"{RESEARCH}abstimmungen/{page}.html"


def speech_url(rede_id: str) -> str:
    return f"{RESEARCH}reden/{page_id(rede_id)}.html"


def top_url(sitting_id: str, position: int) -> str:
    return f"{RESEARCH}sitzungen/{page_id(sitting_id)}.html#top-{position}"


class _Union:
    """Union-find over story nodes ("v:<vorgang id>")."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, nodes: list[str]) -> None:
        for other in nodes[1:]:
            a, b = self.find(nodes[0]), self.find(other)
            if a != b:
                self.parent[max(a, b)] = min(a, b)


def _marks(xs) -> str:
    return ",".join("?" * len(xs))


def _hib(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """The week's hib items with their Drucksache numbers in hib's order (the text only for the heuristic)."""
    items = [
        dict(r) | {"drucksachen": []}
        for r in conn.execute(
            "SELECT id, number, date, title, ressort, kind, committee, source_url url, text FROM hib_item "
            "WHERE date BETWEEN ? AND ? ORDER BY date, CAST(id AS INTEGER)",
            (start, end),
        )
    ]
    by_id = {i["id"]: i for i in items}
    if items and has_table(conn, "hib_drucksache"):
        for r in conn.execute(
            f"SELECT hib_id, drucksache_number FROM hib_drucksache WHERE hib_id IN ({_marks(by_id)}) "
            "ORDER BY hib_id, position",
            list(by_id),
        ):
            by_id[r[0]]["drucksachen"].append(r[1])
    return items


def _drucksachen(conn: sqlite3.Connection, numbers: list[str]) -> dict[str, dict]:
    """Bundestag Drucksachen by number: type, title, date, PDF and their Vorgänge."""
    if not numbers:
        return {}
    out: dict[str, dict] = {}
    for r in conn.execute(
        f"SELECT d.number, d.type, d.title, d.date, d.pdf_url, "
        f"(SELECT json_group_array(vorgang_id) FROM vorgang_drucksache WHERE drucksache_id = d.id) vs "
        f"FROM drucksache d WHERE d.number IN ({_marks(numbers)}) AND coalesce(d.publisher, 'BT') = 'BT'",
        numbers,
    ):
        out[r["number"]] = {"number": r["number"], "type": r["type"], "title": r["title"], "date": r["date"],
                            "pdf": r["pdf_url"] or drucksache_pdf(r["number"]),
                            "vorgaenge": sorted(set(json.loads(r["vs"])))}  # fmt: skip
    return out


def _week_drucksachen(conn: sqlite3.Connection, vids: list[str], start: str, end: str) -> dict[str, list[dict]]:
    """Per Vorgang its Bundestag Drucksachen dated in the week."""
    out: dict[str, list[dict]] = defaultdict(list)
    if not vids:
        return out
    for r in conn.execute(
        f"SELECT vd.vorgang_id v, d.number, d.type, d.title, d.date, d.pdf_url FROM vorgang_drucksache vd "
        f"JOIN drucksache d ON d.id = vd.drucksache_id WHERE vd.vorgang_id IN ({_marks(vids)}) "
        f"AND d.date BETWEEN ? AND ? AND coalesce(d.publisher, 'BT') = 'BT' ORDER BY d.date, d.number",
        [*vids, start, end],
    ):
        out[r["v"]].append({"number": r["number"], "type": r["type"], "title": r["title"], "date": r["date"],
                            "pdf": r["pdf_url"] or drucksache_pdf(r["number"])})  # fmt: skip
    return out


def _agenda(conn: sqlite3.Connection, sittings: list[dict]) -> list[dict]:
    """The week's agenda items with their Vorlagen (Vorgang per Drucksache) and their Reden (no Zwischenfragen, no
    Fragestunde turns), per Vorlage the Reden of its sub-item when the protocol splits them."""
    if not sittings:
        return []
    ids = [s["id"] for s in sittings]
    items = {
        r["id"]: dict(r) | {"vorlagen": [], "reden": []}
        for r in conn.execute(
            f"SELECT a.id, a.sitting_id, a.position, a.top_id, a.title, a.kind, a.no_debate, st.date "
            f"FROM agenda_item a JOIN sitting st ON st.id = a.sitting_id WHERE a.sitting_id IN ({_marks(ids)}) "
            f"ORDER BY st.date, a.position",
            ids,
        )
    }
    if not items:
        return []
    marks = _marks(items)
    if has_table(conn, "agenda_item_vorlage"):
        for r in conn.execute(
            f"SELECT agenda_item_id, sub_item_id, drucksache_number, vorgang_id FROM agenda_item_vorlage "
            f"WHERE agenda_item_id IN ({marks}) ORDER BY id",
            list(items),
        ):
            items[r[0]]["vorlagen"].append({"sub": r[1], "number": r[2], "vorgang": r[3]})
    for r in conn.execute(
        f"SELECT agenda_item_id, sub_item_id, rede_id, person_id, speaker_name, speaker_group, min(position) p "
        f"FROM speech WHERE agenda_item_id IN ({marks}) AND kind = 'rede' AND interruption IS NULL "
        f"GROUP BY agenda_item_id, rede_id ORDER BY sitting_id, p",
        list(items),
    ):
        items[r[0]]["reden"].append({"id": r["rede_id"], "sub": r["sub_item_id"], "person_id": r["person_id"],
                                     "speaker": re.sub(r"\s*\(.*\)$", "", r["speaker_name"]),
                                     "fraction": r["speaker_group"]})  # fmt: skip
    return list(items.values())


def _decisions(conn: sqlite3.Connection, sittings: list[dict]) -> list[dict]:
    """The week's announced decisions with their Vorgänge and fraction positions, plus roll calls the protocol has
    not announced yet (`decision` arrives with the protocol, roll calls a day earlier)."""
    ids = [s["id"] for s in sittings]
    if not ids or not has_table(conn, "decision"):
        return []
    out, seen = [], set()
    rcv = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT id, title, vorgang_id, yes, no, abstain, date FROM roll_call_vote WHERE sitting_id IN ({_marks(ids)})",
        ids)} if has_table(conn, "roll_call_vote") else {}  # fmt: skip
    for r in conn.execute(
        f"SELECT d.id, d.agenda_item_id, d.kind, d.subject, d.result, d.roll_call_vote_id, d.vorgang_id, "
        f"d.drucksache_number, st.date FROM decision d JOIN sitting st ON st.id = d.sitting_id "
        f"WHERE d.sitting_id IN ({_marks(ids)}) ORDER BY st.date, d.position",
        ids,
    ):
        links = [(v, via) for v, via in conn.execute(
            "SELECT vorgang_id, via FROM decision_vorgang WHERE decision_id = ? ORDER BY vorgang_id", (r["id"],))]
        if r["vorgang_id"]:
            vs = [r["vorgang_id"]]
        elif len(links) <= 1 or not (strong := [v for v, via in links if via in ("roll_call", "dip_step")]):
            vs = [v for v, _ in links]
        else:
            vs = strong
        page = page_id(r["roll_call_vote_id"] or r["id"])
        d = {"id": r["id"], "date": r["date"], "agenda_id": r["agenda_item_id"], "kind": r["kind"],
             "subject": r["subject"], "result": r["result"], "drucksache": r["drucksache_number"],
             "vorgaenge": vs, "url": decision_url(page, [v for v, _ in links]),
             "fraktionen": {f: p for f, p in conn.execute(
                 "SELECT fraction, position FROM decision_fraction WHERE decision_id = ?", (r["id"],))}}  # fmt: skip
        if (v := rcv.get(r["roll_call_vote_id"] or "")) is not None:
            seen.add(v["id"])
            d |= {"title": v["title"], "yes": v["yes"], "no": v["no"], "abstain": v["abstain"]}
        out.append(d)
    for v in rcv.values():
        if v["id"] not in seen:
            vs = [v["vorgang_id"]] if v["vorgang_id"] else []
            out.append({"id": v["id"], "date": v["date"], "agenda_id": None, "kind": "namentlich",
                        "subject": v["title"], "title": v["title"], "drucksache": None,
                        "result": "angenommen" if v["yes"] > v["no"] else "abgelehnt", "vorgaenge": vs,
                        "url": decision_url(page_id(v["id"]), vs), "fraktionen": {},
                        "yes": v["yes"], "no": v["no"], "abstain": v["abstain"]})  # fmt: skip
    return out


def _vorgaenge(conn: sqlite3.Connection, vids: list[str]) -> dict[str, dict]:
    """Vorgang id -> type, title, status, subjects, initiators and its lead committees (DIP referrals)."""
    if not vids:
        return {}
    out = {
        r["id"]: {"id": r["id"], "type": r["type"], "title": " ".join(r["title"].split()), "status": r["status"],
                  "subjects": json.loads(r["subjects"] or "[]"), "initiators": json.loads(r["initiators"] or "[]"),
                  "committees": set()}  # fmt: skip
        for r in conn.execute(
            f"SELECT id, type, title, status, subjects, initiators FROM vorgang WHERE id IN ({_marks(vids)})", vids
        )
    }
    if has_table(conn, "vorgang_referral"):
        for v, c in conn.execute(
            f"SELECT vorgang_id, committee FROM vorgang_referral WHERE lead = 1 AND vorgang_id IN ({_marks(vids)})",
            vids,
        ):
            if v in out:
                out[v]["committees"].add(c)
    return out


def _hib_vorgaenge(item: dict, ds: dict[str, dict], info: dict[str, dict]) -> dict[str, str]:
    """The Vorgänge a hib item reports on, each with the Drucksache that leads there. A hearing (JOINING_KINDS) is
    on all it names (up to MAX_HIB_VORGAENGE). Any other item reports on one Drucksache and cites the rest as
    context: the one whose Vorgang title shares most title stems with the item (ties: hib's order), with every
    Vorgang of that Drucksache (a Beschlussempfehlung can close several)."""
    vs: dict[str, str] = {}
    for n in item["drucksachen"]:
        for v in ds.get(n, {}).get("vorgaenge", []):
            if v in info:
                vs.setdefault(v, n)
    if not vs or (item["kind"] in JOINING_KINDS and len(vs) <= MAX_HIB_VORGAENGE):
        return vs
    words = stems(f"{item['title']} {item.get('text', '')[:400]}")
    order = list(dict.fromkeys(vs.values()))
    def shared(n: str) -> int:
        return max(len(words & stems(info[v]["title"])) for v, m in vs.items() if m == n)

    best = max(order, key=lambda n: (shared(n), -order.index(n)))
    if shared(best) == 0 and any(n not in order for n in item["drucksachen"]):
        return {}  # no title in common: the item is on the Drucksache whose Vorgang the store does not have yet
    return {v: n for v, n in vs.items() if n == best}


def _vorgang_signals(v: str, hib_of: dict, ds_of: dict, reden_of: dict, dec_of: dict) -> int:
    dec = dec_of.get(v, [])
    return (bool(hib_of.get(v)) + bool(ds_of.get(v)) + bool(reden_of.get(v)) + bool(dec)
            + any(d["kind"] == "namentlich" for d in dec))  # fmt: skip


def _committee_match(item: dict, story: dict, info: dict[str, dict]) -> bool:
    """A loose hib item may join a story when its committee leads one of the story's Vorgänge or its Ressort is
    the Ressort of one of the story's hib items."""
    committee = (item.get("committee") or "").lower()
    if committee and any(committee == c.lower() for v in story["vorgang_ids"] for c in info[v]["committees"]):
        return True
    return item["ressort"] in {h["ressort"] for h in story["hib"]}


def _attach_loose(loose: list[dict], stories: list[dict], info: dict[str, dict]) -> list[dict]:
    """Joins each hib item without a Vorgang to the Vorgang story it matches best (committee or Ressort, and at
    least MIN_SHARED_WORDS title stems in common); returns the items that stay alone."""
    alone = []
    for item in loose:
        words = stems(item["title"])
        best, best_n = None, 0
        for s in stories:
            if s["key"]["kind"] != "vorgang" or not _committee_match(item, s, info):
                continue
            theirs = set().union(*(stems(info[v]["title"]) for v in s["vorgang_ids"]),
                                 *(stems(h["title"]) for h in s["hib"]))  # fmt: skip
            n = len(words & theirs)
            if n > best_n:
                best, best_n = s, n
        if best is not None and best_n >= MIN_SHARED_WORDS:
            best["hib"].append(item | {"via": f"Ausschuss und {best_n} gemeinsame Stichwörter"})
        else:
            alone.append(item)
    return alone


def _hib_entry(item: dict, via: str) -> dict:
    keep = ("id", "number", "date", "title", "ressort", "kind", "committee", "url", "drucksachen")
    return {k: item[k] for k in keep if item.get(k) is not None} | {"via": via}


def _einbringer(initiators: list[str]) -> list[str]:
    return sorted({einbringer(fraction_name(i)) for i in initiators}, key=EINBRINGER.index)


def _finish(story: dict) -> dict:
    """Signals, score, Fraktion chips and area of an assembled story."""
    sig = {
        "hib": bool(story["hib"]),
        "drucksache": bool(story["drucksachen"]),
        "debatte": any(p["reden"] for p in story["plenum"]),
        "abstimmung": any(not d.get("shared") for d in story["abstimmungen"]),
        "namentlich": any(d["kind"] == "namentlich" and not d.get("shared") for d in story["abstimmungen"]),
    }
    story["signals"] = [k for k in SIGNALS if sig[k]]
    story["quellen"] = ", ".join(SIGNAL_LABEL[k] for k in story["signals"])
    story["score"] = len(story["signals"])
    story["n_hib"] = len(story["hib"])
    story["n_reden"] = len({r["id"] for r in story["reden"]})
    ressorts = Counter(h["ressort"] for h in story["hib"])
    story["area"] = AREA_OF_RESSORT.get(ressorts.most_common(1)[0][0], "weitere") if ressorts else None
    story["hib"].sort(key=lambda h: (h["date"], h["id"]))
    return story


def _plenum_entry(item: dict, reden: list[dict]) -> dict:
    count = Counter(r["fraction"] for r in reden)
    return {"agenda_id": item["id"], "sitting": item["sitting_id"], "date": item["date"], "top": item["top_id"],
            "title": short_title(item["title"], item["top_id"]), "url": top_url(item["sitting_id"], item["position"]),
            "no_debate": bool(item["no_debate"]), "reden": len(reden), "fraktionen": dict(count.most_common())}  # fmt: skip


def _reden_for(item: dict, subs: set[str | None]) -> list[dict]:
    """The Reden of an agenda item that belong to Vorlagen in `subs` (None: the whole item)."""
    if None in subs:
        return item["reden"]
    return [r for r in item["reden"] if r["sub"] is None or r["sub"] in subs]


def _rede_entry(r: dict, agenda_id: str) -> dict:
    return {"id": r["id"], "speaker": r["speaker"], "fraction": r["fraction"], "person_id": r["person_id"],
            "agenda_id": agenda_id, "url": speech_url(r["id"])}  # fmt: skip


def _vorgang_ref(v: dict) -> dict:
    return {"id": v["id"], "title": v["title"], "type": v["type"], "status": v["status"],
            "url": RESEARCH + research_path(v["id"], v["type"])}  # fmt: skip


def assemble(hib: list[dict], ds: dict[str, dict], agenda: list[dict], decisions: list[dict], info: dict[str, dict],
             week_ds: dict[str, list[dict]]) -> list[dict]:
    """The week's stories from the loaded rows, best-carried first (see the module docstring)."""
    union = _Union()
    hib_of: dict[str, list[tuple[dict, str]]] = defaultdict(list)
    loose_ds: dict[str, list[dict]] = defaultdict(list)
    loose: list[dict] = []
    for item in hib:
        if item["ressort"] in OVERVIEW_RESSORTS:
            continue
        vs = _hib_vorgaenge(item, ds, info)
        if vs:
            for v, n in vs.items():
                hib_of[v].append((item, f"Drucksache {n}"))
            if len(vs) > 1:
                union.union([f"v:{v}" for v in vs])
        elif known := [n for n in item["drucksachen"] if n.startswith("21/")]:
            new = [n for n in known if not any(v in info for v in ds.get(n, {}).get("vorgaenge", []))]
            loose_ds[(new or known)[0]].append(item)
        else:
            loose.append(item)
    reden_of: dict[str, list[tuple[dict, set]]] = defaultdict(list)
    debates_alone = []
    for a in agenda:
        subs: dict[str, set] = defaultdict(set)
        for vl in a["vorlagen"]:
            if vl["vorgang"] in info:
                subs[vl["vorgang"]].add(vl["sub"])
        for v, s in subs.items():
            reden_of[v].append((a, s))
        if not a["no_debate"] and a["reden"] and 1 < len(subs) <= MAX_AGENDA_VORGAENGE:
            union.union([f"v:{v}" for v in subs])
        if (not subs and a["reden"] and a["kind"] not in ("befragung", "fragestunde")
                and not (a["title"] or "").startswith("Zur Geschäftsordnung")):
            debates_alone.append(a)
    dec_of: dict[str, list[dict]] = defaultdict(list)
    votes_alone = []
    for d in decisions:
        known = [v for v in d["vorgaenge"] if v in info]
        for v in known:
            dec_of[v].append(d)
        if not known and d["kind"] == "namentlich":
            votes_alone.append(d)
    nodes = set(hib_of) | set(reden_of) | set(dec_of)
    groups: dict[str, list[str]] = defaultdict(list)
    for v in sorted(nodes):
        groups[union.find(f"v:{v}")].append(v)

    def rank(v: str) -> tuple:
        return (TYPE_RANK.get(info[v]["type"], 2), -_vorgang_signals(v, hib_of, week_ds, reden_of, dec_of),
                -len(hib_of.get(v, [])), v)  # fmt: skip

    stories = [_vorgang_story(sorted(vs, key=rank), info, hib_of, reden_of, dec_of, week_ds, ds)
               for vs in groups.values()]  # fmt: skip
    alone = _attach_loose(loose, stories, info)
    stories += [_drucksache_story(n, items, ds) for n, items in loose_ds.items()]
    stories += [_hib_story(item) for item in alone]
    stories += [_debate_story(a) for a in debates_alone]
    stories += [_vote_story(d) for d in votes_alone]
    stories = [_finish(s) for s in stories]
    stories.sort(key=lambda s: (-s["score"], -s["n_hib"], -s["n_reden"], s["id"]))
    return stories


def _vorgang_story(vs: list[str], info, hib_of, reden_of, dec_of, week_ds, ds) -> dict:
    lead = info[vs[0]]
    hib_seen: dict[str, dict] = {}
    for v in vs:
        for item, via in hib_of.get(v, []):
            hib_seen.setdefault(item["id"], _hib_entry(item, via))
    plenum, reden = {}, {}
    for v in vs:
        for a, subs in reden_of.get(v, []):
            rs = _reden_for(a, subs)
            prev = plenum.get(a["id"])
            if prev is None or prev["reden"] < len(rs):
                plenum[a["id"]] = _plenum_entry(a, rs)
            for r in rs:
                reden.setdefault(r["id"], _rede_entry(r, a["id"]))
    decs = {}
    for v in vs:
        for d in dec_of.get(v, []):
            decs[d["id"]] = d | {"shared": not set(d["vorgaenge"]) <= set(vs)}
    drucks = {}
    for v in vs:
        for d in week_ds.get(v, []):
            drucks.setdefault(d["number"], d)
    for h in hib_seen.values():  # the Drucksachen hib links, also older ones
        for n in h["drucksachen"]:
            if n in ds and set(ds[n]["vorgaenge"]) & set(vs):
                drucks.setdefault(n, {k: ds[n][k] for k in ("number", "type", "title", "date", "pdf")})
    initiators = [i for v in vs for i in info[v]["initiators"]]
    return {
        "id": f"v{lead['id']}",
        "key": {"kind": "vorgang", "id": lead["id"]},
        "title": lead["title"],
        "type": lead["type"],
        "url": RESEARCH + research_path(lead["id"], lead["type"]),
        "stage": stage(lead["type"], lead["status"]),
        "vorgang_ids": vs,
        "vorgaenge": [_vorgang_ref(info[v]) for v in vs],
        "einbringer": _einbringer(initiators),
        "sachgebiete": sorted({s for v in vs for s in info[v]["subjects"]}),
        "hib": list(hib_seen.values()),
        "drucksachen": sorted(drucks.values(), key=lambda d: (d["date"], d["number"])),
        "plenum": sorted(plenum.values(), key=lambda p: (p["date"], p["agenda_id"])),
        "reden": list(reden.values()),
        "abstimmungen": [_vote_entry(d) for d in sorted(decs.values(), key=lambda d: (d["date"], d["id"]))],
    }


def _vote_entry(d: dict) -> dict:
    keep = ("id", "date", "kind", "subject", "title", "result", "url", "yes", "no", "abstain", "fraktionen", "shared")
    return {k: d[k] for k in keep if d.get(k) is not None and d.get(k) != {}}


def _empty(id_: str, kind: str, title: str, url: str | None) -> dict:
    return {"id": id_, "key": {"kind": kind, "id": id_.split("-", 1)[1]}, "title": title, "type": None, "url": url,
            "stage": None, "vorgang_ids": [], "vorgaenge": [], "einbringer": [], "sachgebiete": [], "hib": [],
            "drucksachen": [], "plenum": [], "reden": [], "abstimmungen": []}  # fmt: skip


def _drucksache_story(number: str, items: list[dict], ds: dict) -> dict:
    """hib items on a Drucksache DIP has not delivered (or not tied to a Vorgang) yet."""
    d = ds.get(number)
    s = _empty(f"ds-{page_id(number)}", "drucksache", d["title"] if d else items[0]["title"], None)
    s["hib"] = [_hib_entry(i, f"Drucksache {number}") for i in items]
    s["drucksachen"] = [{k: d[k] for k in ("number", "type", "title", "date", "pdf")} if d else
                        {"number": number, "pdf": drucksache_pdf(number)}]  # fmt: skip
    s["title_source"] = "dip" if d else "hib"
    return s


def _hib_story(item: dict) -> dict:
    s = _empty(f"hib-{item['id']}", "hib", item["title"], item["url"])
    s["hib"] = [_hib_entry(item, "ohne Drucksache")]
    s["title_source"] = "hib"
    return s


def _debate_story(a: dict) -> dict:
    """A debate without Vorlagen: an Aktuelle Stunde or a vereinbarte Debatte."""
    title = a["title"] or a["top_id"]
    segments = [p.strip() for p in title.split("|")]
    if segments[0] in ("Aktuelle Stunde", "Vereinbarte Debatte:") and len(segments) > 1:
        label = segments[0].rstrip(":")
        topic = next((p for p in segments[1:] if not p.lower().startswith("auf verlangen")), segments[-1])
        title = f"{label}: {topic}"
    else:
        title = short_title(a["title"], a["top_id"])
    s = _empty(f"top-{page_id(a['id'])}", "agenda", title, top_url(a["sitting_id"], a["position"]))
    s["plenum"] = [_plenum_entry(a, a["reden"])]
    s["reden"] = [_rede_entry(r, a["id"]) for r in a["reden"]]
    if requested := _verlangen(a["title"]):
        s["einbringer"] = sorted({einbringer(f) for f in requested}, key=EINBRINGER.index)
    return s


def _verlangen(title: str | None) -> list[str]:
    from radar.origin import aktuelle_stunde

    return aktuelle_stunde(title) or []


def _vote_story(d: dict) -> dict:
    """A roll call whose Vorgang the store does not know (W41: linking fixed later in the foundation)."""
    s = _empty(f"abst-{page_id(d['id'])}", "vote", d.get("title") or d["subject"], d["url"])
    s["abstimmungen"] = [_vote_entry(d)]
    return s


def load(conn: sqlite3.Connection, week: str | None = None, today: str | None = None) -> dict | None:
    """The stories of `week` (default: the newest hib week); None without hib in the store."""
    week = week or latest_week(conn, today)
    if week is None:
        return None
    start, end = week_range(week)
    sittings = [dict(r) for r in conn.execute(
        "SELECT id, date, preliminary FROM sitting WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))]  # fmt: skip
    hib = _hib(conn, start, end) if has_table(conn, "hib_item") else []
    numbers = sorted({n for i in hib for n in i["drucksachen"]})
    ds = _drucksachen(conn, numbers) if has_table(conn, "drucksache") else {}
    agenda = _agenda(conn, sittings)
    decisions = _decisions(conn, sittings)
    vids = sorted({v for d in ds.values() for v in d["vorgaenge"]}
                  | {vl["vorgang"] for a in agenda for vl in a["vorlagen"] if vl["vorgang"]}
                  | {v for d in decisions for v in d["vorgaenge"]})  # fmt: skip
    info = _vorgaenge(conn, vids) if has_table(conn, "vorgang") else {}
    week_ds = _week_drucksachen(conn, sorted(info), start, end) if has_table(conn, "vorgang_drucksache") else {}
    stories = assemble(hib, ds, agenda, decisions, info, week_ds)
    for s in stories:
        s.pop("vorgang_ids", None)
    return {
        "week": week, "start": start, "end": end,
        "sittings": [{"id": s["id"], "date": s["date"], "preliminary": bool(s["preliminary"])} for s in sittings],
        "method": METHOD,
        "params": {"max_agenda_vorgaenge": MAX_AGENDA_VORGAENGE, "max_hib_vorgaenge": MAX_HIB_VORGAENGE,
                   "min_shared_words": MIN_SHARED_WORDS, "signals": list(SIGNALS)},
        "counts": {"hib": len(hib), "stories": len(stories),
                   "by_score": dict(sorted(Counter(s["score"] for s in stories).items(), reverse=True))},
        "stories": stories,
    }  # fmt: skip


def write(out: Path, conn: sqlite3.Connection, week: str | None = None) -> dict | None:
    """`stories.json` in `out`; returns the payload (None and no file without hib)."""
    data = load(conn, week)
    path = out / FILE
    if data is None:
        path.unlink(missing_ok=True)
        return None
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


# ---------------------------------------------------------------------------------------------------- rendering

CHIP = {"BÜNDNIS 90/DIE GRÜNEN": "Grüne"}  # as Research's pages and the hib mosaic name it
FRACTION_CLASS = {"CDU/CSU": "cdu", "SPD": "spd", "AfD": "afd", "BÜNDNIS 90/DIE GRÜNEN": "gru", "Die Linke": "lin"}

CSS = """
.stories{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));margin:12px 0}
.story{border:1px solid #e4e0f0;border-radius:10px;padding:12px 14px;background:#fff;display:flex;
  flex-direction:column;gap:6px}
.story h3{font-size:1rem;line-height:1.3;margin:0}
.story h3 a{color:inherit;text-decoration:none}.story h3 a:hover{text-decoration:underline}
.story .meta{font-size:.8rem;color:#5b5670;display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center}
.story .quellen{font-size:.8rem;color:#7c3aed;font-weight:600}
.story .stage{background:#f1ebff;color:#5b21b6;border-radius:999px;padding:1px 8px}
.story .chip{border-radius:4px;padding:0 6px;font-size:.75rem;background:#eee;color:#222}
.story .chip.cdu{background:#222;color:#fff}.story .chip.spd{background:#e3000f;color:#fff}
.story .chip.afd{background:#009ee0;color:#fff}.story .chip.gru{background:#1aa037;color:#fff}
.story .chip.lin{background:#be3075;color:#fff}
.story .kurz{font-size:.92rem;line-height:1.45;margin:2px 0}
.story details{font-size:.85rem}.story details summary{cursor:pointer;color:#5b5670}
.story ul{margin:4px 0 0;padding-left:18px}.story li{margin:2px 0}
.story a.ev{color:#2563eb}
.story .positions{font-size:.88rem;margin:0;padding-left:18px}
.radar-gen{font-size:.78rem;color:#5b5670}
"""


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _chips(names: list[str]) -> str:
    return "".join(f'<span class="chip {FRACTION_CLASS.get(n, "")}">{_e(CHIP.get(n, n))}</span>' for n in names)


def _sources(s: dict) -> str:
    """The evidence list of a card: every hib item, Drucksache, TOP, vote and the Reden, each a link."""
    lines = [f'<li><a class="ev" href="{_e(h["url"])}">hib {_e(h["number"])}</a> {_e(h["title"])}</li>'
             for h in s["hib"]]  # fmt: skip
    lines += [f'<li><a class="ev" href="{_e(d["pdf"])}">Drucksache {_e(d["number"])}</a> '
              f'{_e(d.get("type") or "")}</li>' for d in s["drucksachen"][:6]]  # fmt: skip
    if len(s["drucksachen"]) > 6:
        lines.append(f"<li>und {len(s['drucksachen']) - 6} weitere Drucksachen</li>")
    for p in s["plenum"]:
        what = "ohne Aussprache" if p["no_debate"] or not p["reden"] else f"{p['reden']} Reden"
        lines.append(f'<li><a class="ev" href="{_e(p["url"])}">Plenum {_e(p["date"])}</a> '
                     f'{_e(p["top"])}, {what}</li>')  # fmt: skip
    for d in s["abstimmungen"]:
        kind = "Namentliche Abstimmung" if d["kind"] == "namentlich" else "Abstimmung"
        votes = f" ({d['yes']} Ja, {d['no']} Nein, {d['abstain']} Enthaltungen)" if "yes" in d else ""
        lines.append(f'<li><a class="ev" href="{_e(d["url"])}">{kind}</a> {_e(d.get("result") or "ohne Ergebnis")}'
                     f"{votes}: {_e(d.get('title') or d['subject'])}</li>")  # fmt: skip
    if len(s["vorgaenge"]) > 1:
        lines += [f'<li><a class="ev" href="{_e(v["url"])}">Vorgang</a> {_e(v["title"])}</li>'
                  for v in s["vorgaenge"][1:]]  # fmt: skip
    return "<ul>" + "".join(lines) + "</ul>"


def render_card(s: dict, extra: dict | None = None) -> str:
    """One story card; `extra` is the story's entry of lead.json (kurz erklärt, positions), when generated."""
    extra = extra or {}
    title = f'<a href="{_e(s["url"])}">{_e(s["title"])}</a>' if s.get("url") else _e(s["title"])
    meta = []
    if s.get("stage"):
        meta.append(f'<span class="stage" title="Beratungsstand im DIP: {_e(s["stage"]["dip"])}">'
                    f'{_e(s["stage"]["label"])}</span>')  # fmt: skip
    if s.get("type"):
        meta.append(_e(s["type"]))
    if s["einbringer"]:
        meta.append(_chips(s["einbringer"]))
    if s["n_reden"]:
        meta.append(f"{s['n_reden']} Reden")
    parts = [f"<h3>{title}</h3>", f'<div class="meta">{"".join(f"<span>{m}</span>" for m in meta)}</div>',
             f'<div class="quellen">Quellen: {_e(s["quellen"])}</div>']  # fmt: skip
    if extra.get("kurz"):
        parts.append(f'<p class="kurz">{extra["kurz"]}</p>')  # already escaped and linked by lead.render_text
    if extra.get("positions"):
        lis = "".join(f"<li>{p}</li>" for p in extra["positions"])
        parts.append(f'<details open><summary>Positionen in der Debatte</summary><ul class="positions">{lis}</ul>'
                     "</details>")  # fmt: skip
    parts.append(f"<details><summary>Belege</summary>{_sources(s)}</details>")
    return f'<article class="story" id="{_e(s["id"])}">{"".join(parts)}</article>'


def render_section(payload: dict, lead: dict | None = None, limit: int = 12) -> str:
    """The stories of the week as an HTML fragment (cards), for the start page; `lead` is lead.json, whose
    "kurz erklärt" and positions go on the cards it covers."""
    extras = (lead or {}).get("stories", {})
    cards = "".join(render_card(s, extras.get(s["id"])) for s in payload["stories"][:limit])
    return (f'<section class="radar-stories"><style>{CSS}</style><h2>Geschichten der Woche</h2>'
            f'<div class="stories">{cards}</div><p class="radar-gen">{_e(payload["method"])}</p></section>')  # fmt: skip


def lab_page(payload: dict, lead: dict | None = None) -> str:
    """The standalone preview data/out/lab-stories.html: lead text above the cards."""
    from radar import lead as lead_mod

    top = lead_mod.render_section(lead) if lead else ""
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Die Woche im Bundestag {_e(payload["week"])} – Radar (Labor)</title>
<link rel="stylesheet" href="{_e(RESEARCH)}shell.css">
<style>body{{font-family:Inter,system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:12px 14px;color:#1d1b26}}
h1{{font-size:1.4rem;color:#7c3aed;margin:8px 0}}h2{{font-size:1.15rem;margin:18px 0 6px}}</style></head><body>
<h1>Die Woche im Bundestag</h1><p class="radar-gen">{_e(payload["start"])} bis {_e(payload["end"])}</p>
{top}{render_section(payload, lead)}</body></html>"""
