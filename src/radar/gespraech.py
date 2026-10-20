"""„Im Gespräch“: which members of the Bundestag and which topics get unusual attention this week.

Attention is not importance and not a rating, so every number here is compared with the same person's or topic's
own past, never with other people: Merz always has more Wikipedia readers than a backbencher, and that says nothing
about the week. A person appears only when at least two signals are clearly above their own 90 days before the week,
or when their Wikipedia page is read far more than usual *and* they did something in the Bundestag that week (a
speech, a Zwischenfrage, an answer in the Befragung, a vote against their Fraktion, a hib mention): the page is about
the Bundestag, not about the news.

Signals per person (research 02-hot-signals.md):

- Wikipedia pageviews (Wikimedia REST API, de.wikipedia, human readers): the week's sum against seven times the
  median day of the 90 days before. Articles come from the person's Wikidata QID, never from a name search.
- Plenary, compared per sitting week of the baseline (Sitzungswochen differ from quiet weeks, the summer break
  would drown everything else): Reden, Zurufe/Lachen/Widerspruch received per 1,000 words, Zwischenfragen asked and
  received, answers in the Regierungsbefragung and Fragestunde.
- hib mentions: the full name (never the surname alone: "Bas", "Frei", "Bär" are words, 30 surnames are shared by
  two or more MdBs) in the week's hib texts. hib texts are read for matching only; the page links the articles.
- optional, external: tagesschau search hits whose title or teaser carry the name (counts and links only).

Topics: the share of each DIP Sachgebiet among new Drucksachen (28 days against the 182 before, shares not counts
because of the summer break), the week's Aktuelle Stunden with the Fraktion that asked for them, and the
Regierungsbefragung (who answered, who asked). Everything links its evidence on Research."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import sqlite3
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from radar import shell
from radar.corpus import short_title, week_id
from radar.origin import fraction_name

FILE = "gespraech.json"
PREVIEW = "lab-gespraech.html"
USER_AGENT = "plenar-radar.de Radar (https://plenar-radar.de; jan.c.buchkremer@gmail.com)"
CACHE = Path("data/cache")
RADAR = "#7c3aed"

WINDOW = 7  # days of "this week"
BASELINE_DAYS = 90  # the person's own past, before the window
TOPIC_RECENT_DAYS, TOPIC_BASE_DAYS = 28, 182

# Wikipedia: (week + K) / (baseline week + K); K damps small articles (30 → 90 readers is no event)
PV_SMOOTH = 70
PV_ABOVE = 2.0  # "clearly above": twice the usual week …
PV_ABOVE_EXTRA = 300  # … and at least this many more readers
PV_EXTREME = 4.0  # alone enough, with a parliamentary connection
PV_EXTREME_EXTRA = 2000
PV_NEWS = 20.0  # a news event about the person (a resignation, an appointment): shows without a plenary link …
PV_NEWS_EXTRA = 20000  # … when this many more readers than usual came in the week
# counts per sitting week: (week + 1) / (baseline + 1) >= COUNT_FACTOR and at least COUNT_MIN in the week
COUNT_FACTOR, COUNT_MIN = 2.0, 2
# Zurufe per 1,000 words: the baseline shrunk towards the house's rate with RATE_PRIOR_WORDS words
RATE_FACTOR, RATE_MIN_EVENTS, RATE_MIN_WORDS, RATE_PRIOR_WORDS = 1.75, 8, 600, 1500
REACTIONS = ("zuruf", "widerspruch", "lachen")  # not beifall, not the speaker's own side's gegenruf
TOPIC_FLOOR = 6  # Drucksachen of a Sachgebiet in the 28 days before it can show
TOPIC_FACTOR = 1.4
TOPIC_SMOOTH = 2
MAX_EVIDENCE = 6

FRACTION_SHORT = {"BÜNDNIS 90/DIE GRÜNEN": "Grüne", "fraktionslos": "fraktionslos"}
GOV_KINDS = ("kanzler", "minister")  # federal ministers without a seat are part of the people list
PRESENT_ROLES = {"kanzler", "minister", "staatsminister", "parl_sts"}


# ---------------------------------------------------------------- dates


def window(date: str) -> tuple[str, str, str, str]:
    """The week ending on `date` and the baseline before it: (week start, week end, base start, base end)."""
    end = dt.date.fromisoformat(date)
    start = end - dt.timedelta(days=WINDOW - 1)
    base_end = start - dt.timedelta(days=1)
    base_start = start - dt.timedelta(days=BASELINE_DAYS)
    return start.isoformat(), end.isoformat(), base_start.isoformat(), base_end.isoformat()


def days(start: str, end: str) -> list[str]:
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    return [(d0 + dt.timedelta(n)).isoformat() for n in range((d1 - d0).days + 1)]


def page_id(entity_id: str) -> str:
    """Research's file name for an id with slashes ("21/99" -> "21-99")."""
    return entity_id.replace("/", "-")


def slug(name: str) -> str:
    """Research's `urls.slug`: "Europapolitik und Europäische Union" -> "europapolitik-und-europaeische-union"."""
    umlaut = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
    return re.sub(r"[^a-z0-9]+", "-", name.translate(umlaut).lower()).strip("-")


def speech_path(speech_id: str) -> str:
    """Research's `urls.speech`: the rede's page, a part as an anchor."""
    rede = re.sub(r"-\d+$", "", speech_id)
    return f"reden/{page_id(rede)}.html" + ("" if rede == speech_id else f"#{speech_id}")


# ---------------------------------------------------------------- signal maths


@dataclass
class Signal:
    """One signal of one person: the week's value, the usual value, whether it is clearly above."""

    key: str
    week: float
    base: float
    above: bool
    extreme: bool = False
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"key": self.key, "week": round(self.week, 1), "base": round(self.base, 1), "above": self.above,
                **({"extreme": True} if self.extreme else {}), **self.detail}  # fmt: skip


def pageview_signal(daily: dict[str, int], week_days: list[str], base_days: list[str]) -> Signal | None:
    """The week's readers against seven median days of the baseline. The median (not the mean) keeps one viral
    day in the baseline from hiding the next one; missing days count as missing, not as zero. None without data."""
    base = [daily[d] for d in base_days if d in daily]
    week = [daily[d] for d in week_days if d in daily]
    if len(base) < 30 or not week:
        return None
    week_sum = sum(week) * WINDOW / len(week)  # a missing last day (pageviews arrive a day late) is extrapolated
    base_week = statistics.median(base) * WINDOW
    ratio = (week_sum + PV_SMOOTH) / (base_week + PV_SMOOTH)
    extra = week_sum - base_week
    above = ratio >= PV_ABOVE and extra >= PV_ABOVE_EXTRA
    extreme = ratio >= PV_EXTREME and extra >= PV_EXTREME_EXTRA
    news = ratio >= PV_NEWS and extra >= PV_NEWS_EXTRA
    peak = max((d for d in week_days if d in daily), key=lambda d: daily[d])
    detail = {"ratio": round(ratio, 2), "days": len(week), "peak": peak, "peak_views": daily[peak],
              "series": [daily.get(d) for d in week_days], **({"news": True} if news else {})}  # fmt: skip
    return Signal("wikipedia", week_sum, base_week, above, extreme, detail)


def count_signal(key: str, week: int, base_total: int, base_weeks: int, detail: dict | None = None) -> Signal:
    """A count in the week against the person's own count per sitting week of the baseline (+1 smoothing: going
    from 0 to 1 is no event, 0 to 3 can be)."""
    base = base_total / base_weeks if base_weeks else 0.0
    above = week >= COUNT_MIN and (week + 1) / (base + 1) >= COUNT_FACTOR
    return Signal(key, week, base, above, detail=detail or {})


def rate_signal(events: int, words: int, base_events: int, base_words: int, house_rate: float) -> Signal:
    """Reactions per 1,000 words in the week against the person's own rate, shrunk towards the house's rate so
    that a person with one short baseline speech does not get an extreme baseline."""
    base_rate = (base_events + house_rate * RATE_PRIOR_WORDS / 1000) / (base_words + RATE_PRIOR_WORDS) * 1000
    rate = events / words * 1000 if words else 0.0
    above = words >= RATE_MIN_WORDS and events >= RATE_MIN_EVENTS and rate >= RATE_FACTOR * base_rate
    return Signal("zurufe", rate, base_rate, above, detail={"events": events, "words": words})


def include(signals: dict[str, Signal], connected: bool) -> str | None:
    """Whether a person shows and in which group: None, "nachricht" (Wikipedia twenty times above: news about a
    member or minister, such as a resignation, is the week's political event even without a speech), "beides"
    (Wikipedia and the Bundestag), "plenum" (only parliamentary signals) or "wikipedia" (Wikipedia far above, plus
    something in the Bundestag)."""
    above = [k for k, s in signals.items() if s.above]
    wiki = signals.get("wikipedia")
    if wiki is not None and wiki.detail.get("news"):
        return "nachricht"
    if len(above) >= 2:
        return "beides" if "wikipedia" in above else "plenum"
    if wiki is not None and wiki.extreme and connected:
        return "wikipedia"
    return None


# ---------------------------------------------------------------- people in the store


def people(conn: sqlite3.Connection, start: str, end: str) -> dict[str, dict]:
    """Current MdBs (a WP 21 fraction membership without end) and the Kanzler and federal ministers in office
    during the window, with name, Fraktion, QID and the office that drives attention."""
    out: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT DISTINCT p.id, p.first_name, p.last_name, p.name_prefix, p.wikidata_qid, m.name fraction "
        "FROM person p JOIN membership m ON m.person_id = p.id "
        "WHERE m.kind = 'fraction' AND m.wahlperiode = 21 AND m.to_date IS NULL"
    ):
        out[r["id"]] = {"id": r["id"], "first": r["first_name"], "last": r["last_name"], "prefix": r["name_prefix"],
                        "qid": r["wikidata_qid"], "fraction": r["fraction"], "roles": []}  # fmt: skip
    for r in conn.execute(
        "SELECT g.person_id, g.office, g.kind, g.name, g.from_date, g.to_date, p.first_name, p.last_name, "
        "p.name_prefix, p.wikidata_qid, p.party FROM government_role g JOIN person p ON p.id = g.person_id "
        "WHERE g.from_date <= ? AND (g.to_date IS NULL OR g.to_date >= ?) ORDER BY g.from_date",
        (end, start),
    ):
        if r["kind"] not in PRESENT_ROLES:
            continue
        if r["person_id"] not in out:
            if r["kind"] not in GOV_KINDS or not r["wikidata_qid"]:
                continue
            out[r["person_id"]] = {"id": r["person_id"], "first": r["first_name"], "last": r["last_name"],
                                   "prefix": r["name_prefix"], "qid": r["wikidata_qid"], "fraction": None,
                                   "party": r["party"], "roles": []}  # fmt: skip
        role = {"office": r["office"], "kind": r["kind"]}
        if r["to_date"] and r["to_date"] < end:
            role["until"] = r["to_date"]
        if r["from_date"] > start:
            role["since"] = r["from_date"]
        out[r["person_id"]]["roles"].append(role)
    for r in conn.execute(
        "SELECT DISTINCT person_id, name, role FROM membership WHERE wahlperiode = 21 AND to_date IS NULL AND "
        "((kind = 'fraction' AND (role IN ('Vorsitzende', 'Vorsitzender') OR role LIKE '%Vorsitzender der CSU%')) "
        "OR (kind = 'other' AND name = 'Präsidium'))"
    ):
        if r["person_id"] in out:
            office = (
                r["role"]
                if r["name"] == "Präsidium"
                else ("Vorsitz der CSU-Landesgruppe" if "CSU" in r["role"] else f"Fraktionsvorsitz {short(r['name'])}")
            )
            out[r["person_id"]]["roles"].append({"office": office, "kind": "fraktion"})
    for p in out.values():
        p["name"] = full_name(p)
        p["role"] = role_label(p["roles"])
    return out


def short(fraction: str | None) -> str:
    return FRACTION_SHORT.get(fraction or "", fraction or "")


def full_name(p: dict) -> str:
    """The name as protocols print it: the first given name, the prefix, the surname ("Konstantin von Notz")."""
    return " ".join(x for x in (p["first"].split()[0] if p["first"] else "", p.get("prefix") or "", p["last"]) if x)


def role_label(roles: list[dict]) -> str | None:
    """The office that explains attention, government first (its stammdaten Fraktionsvorsitz may be stale)."""
    gov = [r for r in roles if r["kind"] != "fraktion"]
    if gov:
        order = ["kanzler", "minister", "staatsminister", "parl_sts"]
        r = sorted(gov, key=lambda r: (order.index(r["kind"]), "until" in r))[0]
        office = r["office"].replace(" der Bundesrepublik Deutschland", "")
        if "until" in r:
            return f"{office} bis {de_date(r['until'])}"
        if "since" in r:
            return f"{office} seit {de_date(r['since'])}"
        return office
    return roles[0]["office"] if roles else None


def de_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day}.{d.month}.{d.year}"


# ---------------------------------------------------------------- plenary


def sitting_weeks(conn: sqlite3.Connection, start: str, end: str) -> int:
    rows = conn.execute("SELECT DISTINCT date FROM sitting WHERE date BETWEEN ? AND ?", (start, end))
    return len({week_id(r[0]) for r in rows})


def plenary(conn: sqlite3.Connection, start: str, end: str) -> tuple[dict[str, dict], float]:
    """Per person between start and end: Reden (main speaker of a rede outside Befragung and Fragestunde), their
    words, the Zurufe/Lachen/Widerspruch printed during them, Zwischenfragen asked and received, answers in the
    Befragung and Fragestunde. Also the house's reactions per 1,000 words (the prior of `rate_signal`)."""
    stats: dict[str, dict] = defaultdict(lambda: {"reden": [], "words": 0, "zurufe": 0, "zf_asked": [],
                                                  "zf_received": [], "answers": [], "chair_speeches": 0})  # fmt: skip
    rows = conn.execute(
        "SELECT s.id, s.rede_id, s.person_id, s.text, s.interruption, st.date, a.id agenda_id, a.position, a.title, "
        "a.top_id, s.sitting_id FROM speech s JOIN sitting st ON st.id = s.sitting_id "
        "LEFT JOIN agenda_item a ON a.id = s.agenda_item_id "
        "WHERE st.date BETWEEN ? AND ? AND s.kind = 'rede' "
        "AND coalesce(a.kind, '') NOT IN ('befragung', 'fragestunde') "
        "AND s.id NOT IN (SELECT speech_id FROM question_turn) ORDER BY s.sitting_id, s.position",
        (start, end),
    ).fetchall()
    main = {r["rede_id"]: r["person_id"] for r in rows if r["id"] == r["rede_id"]}
    main_parts = {}
    for r in rows:
        speaker = main.get(r["rede_id"])
        if r["interruption"] == "zwischenfrage":
            ref = {"id": r["id"], "date": r["date"], "url": speech_path(r["id"])}
            stats[r["person_id"]]["zf_asked"].append(ref)
            if speaker:
                stats[speaker]["zf_received"].append(ref)
        elif r["person_id"] == speaker and not r["interruption"]:
            main_parts[r["id"]] = speaker
            stats[speaker]["words"] += len(r["text"].split())
            if r["id"] == r["rede_id"]:
                stats[speaker]["reden"].append({
                    "id": r["id"], "date": r["date"], "url": speech_path(r["id"]),
                    "top": short_title(r["title"], r["top_id"] or ""),
                    "top_url": f"sitzungen/{page_id(r['sitting_id'])}.html#top-{r['position']}"
                    if r["position"] is not None else None,
                })  # fmt: skip
    total_events = 0
    marks = ",".join("?" * len(REACTIONS))
    for r in conn.execute(
        f"SELECT i.speech_id, count(*) n FROM interjection i JOIN speech s ON s.id = i.speech_id "
        f"JOIN sitting st ON st.id = s.sitting_id WHERE st.date BETWEEN ? AND ? AND i.kind IN ({marks}) "
        f"GROUP BY i.speech_id",
        (start, end, *REACTIONS),
    ):
        if r["speech_id"] in main_parts:
            stats[main_parts[r["speech_id"]]]["zurufe"] += r["n"]
            total_events += r["n"]
    for r in conn.execute(
        "SELECT s.person_id, s.sitting_id, a.kind, count(*) n FROM question_turn q JOIN speech s ON s.id = q.speech_id "
        "JOIN sitting st ON st.id = s.sitting_id JOIN agenda_item a ON a.id = s.agenda_item_id "
        "WHERE q.role = 'antwort' AND st.date BETWEEN ? AND ? GROUP BY 1, 2, 3",
        (start, end),
    ):
        url = f"regierung/regierungsbefragung/{page_id(r['sitting_id'])}.html" if r["kind"] == "befragung" else None
        stats[r["person_id"]]["answers"].append({"sitting": r["sitting_id"], "kind": r["kind"], "n": r["n"],
                                                 **({"url": url} if url else {})})  # fmt: skip
    total_words = sum(s["words"] for s in stats.values())
    return dict(stats), (total_events / total_words * 1000 if total_words else 0.0)


def deviations(conn: sqlite3.Connection, start: str, end: str) -> dict[str, list[dict]]:
    """Roll-call votes where a person voted against the majority of their own Fraktion (absent not counted)."""
    out: dict[str, list[dict]] = defaultdict(list)
    for v in conn.execute("SELECT id, title, date FROM roll_call_vote WHERE date BETWEEN ? AND ?", (start, end)):
        rows = conn.execute(
            "SELECT person_id, fraction, vote FROM individual_vote "
            "WHERE vote_id = ? AND vote IN ('yes','no','abstain')",
            (v["id"],),
        ).fetchall()
        majority = {}
        for f in {r["fraction"] for r in rows}:
            majority[f] = Counter(r["vote"] for r in rows if r["fraction"] == f).most_common(1)[0][0]
        for r in rows:
            if r["person_id"] and r["fraction"] != "fraktionslos" and r["vote"] != majority[r["fraction"]]:
                out[r["person_id"]].append({"vote": v["id"], "title": v["title"], "date": v["date"],
                                            "url": f"abstimmungen/{page_id(v['id'])}.html"})  # fmt: skip
    return dict(out)


# ---------------------------------------------------------------- hib mentions

_TITLES = re.compile(r"\b(Dr\.|Prof\.|h\. ?c\.|Dipl\.-\w+\.?)\s*")


def name_variants(conn: sqlite3.Connection, persons: dict[str, dict]) -> tuple[dict[str, str], list[str]]:
    """Full-name spellings -> person id: official first name(s) + prefix + surname, the first given name only, the
    names the protocols print for the person and the government role's label. A spelling that fits two people is
    dropped (returned second), so a namesake never gets another's mention. Surnames alone are never used."""
    spellings: dict[str, set[str]] = defaultdict(set)
    for pid, p in persons.items():
        firsts = {p["first"], p["first"].split()[0]} if p["first"] else set()
        for f in firsts:
            spellings[f"{f} {p['last']}"].add(pid)
            if p.get("prefix"):
                spellings[f"{f} {p['prefix']} {p['last']}"].add(pid)
    ids = list(persons)
    for chunk in range(0, len(ids), 500):
        part = ids[chunk : chunk + 500]
        for r in conn.execute(
            f"SELECT DISTINCT person_id, speaker_name FROM speech WHERE person_id IN ({','.join('?' * len(part))})",
            part,
        ):
            name = _TITLES.sub("", r["speaker_name"].split(",")[0].split("(")[0]).strip()
            if len(name.split()) >= 2:
                spellings[name].add(r["person_id"])
        for r in conn.execute(
            f"SELECT DISTINCT person_id, name FROM government_role WHERE person_id IN ({','.join('?' * len(part))})",
            part,
        ):
            spellings[r["name"]].add(r["person_id"])
    variants = {s: next(iter(p)) for s, p in spellings.items() if len(p) == 1 and len(s.split()) >= 2}
    ambiguous = sorted(s for s, p in spellings.items() if len(p) > 1)
    return variants, ambiguous


def name_pattern(variants: dict[str, str]) -> re.Pattern:
    """One regex over all spellings, longest first, on word boundaries ("Bärbel Bas" not in "Bärbel Bastian")."""
    alts = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])({alts})(?![\w-])")


def mentions(texts: list[tuple[str, str]], variants: dict[str, str], first_names: set[str] | None = None
             ) -> dict[str, set[str]]:  # fmt: skip
    """person id -> ids of the texts that name them in full. A match right after another given name is part of a
    longer name ("Hans Christian Schmidt" is not "Christian Schmidt") and is skipped."""
    if not variants:
        return {}
    pattern = name_pattern(variants)
    first_names = first_names or set()
    out: dict[str, set[str]] = defaultdict(set)
    for tid, text in texts:
        for m in pattern.finditer(text):
            before = text[max(0, m.start() - 30) : m.start()].split()
            if before and m.start() > 0 and text[m.start() - 1] == " " and before[-1] in first_names:
                continue
            out[variants[m.group(1)]].add(tid)
    return dict(out)


def hib_texts(conn: sqlite3.Connection, start: str, end: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, date, title, text, source_url FROM hib_item WHERE date BETWEEN ? AND ? ORDER BY date", (start, end)
    ).fetchall()


# ---------------------------------------------------------------- network (Wikidata, Wikimedia, tagesschau)


def fetch_json(url: str, cache: Path, sleep: float = 0.05, not_found: dict | None = None) -> dict | None:
    """GET a JSON document once: the raw response is kept in `cache` and read from there on every later run.
    Serial and polite (own User-Agent with contact, a short pause). A 404 is cached as `not_found` when given;
    other errors return None and are not cached (the next run tries again)."""
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404 and not_found is not None:
            body = json.dumps(not_found)
        else:
            print(f"  {e.code} {url}")
            return None
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  {e} {url}")
        return None
    finally:
        time.sleep(sleep)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(body, encoding="utf-8")
    return json.loads(body)


def wiki_titles(qids: list[str], cache: Path = CACHE) -> dict[str, str]:
    """QID -> title of the German Wikipedia article, 50 QIDs per Wikidata request."""
    out: dict[str, str] = {}
    qids = sorted(set(qids))
    for i in range(0, len(qids), 50):
        batch = qids[i : i + 50]
        key = hashlib.sha1("|".join(batch).encode()).hexdigest()[:16]
        url = ("https://www.wikidata.org/w/api.php?action=wbgetentities&props=sitelinks&sitefilter=dewiki"
               f"&format=json&ids={'|'.join(batch)}")  # fmt: skip
        data = fetch_json(url, cache / "wikidata" / f"{key}.json", sleep=0.2) or {}
        for qid, e in data.get("entities", {}).items():
            title = e.get("sitelinks", {}).get("dewiki", {}).get("title")
            if title:
                out[qid] = title
    return out


def pageviews(title: str, start: str, end: str, cache: Path = CACHE) -> dict[str, int]:
    """Daily human (agent=user) views of a de.wikipedia article, ISO date -> views."""
    article = urllib.parse.quote(title.replace(" ", "_"), safe="")
    a, b = start.replace("-", ""), end.replace("-", "")
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/de.wikipedia/all-access/user/"
           f"{article}/daily/{a}/{b}")  # fmt: skip
    name = hashlib.sha1(title.encode()).hexdigest()[:16]
    data = fetch_json(url, cache / "pageviews" / f"{name}_{a}_{b}.json", not_found={"items": []}) or {}
    return {f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}-{i['timestamp'][6:8]}": i["views"]
            for i in data.get("items", [])}  # fmt: skip


def tagesschau(name: str, start: str, end: str, cache: Path = CACHE) -> dict | None:
    """tagesschau search hits for a full name, counted only when the name is in the title or teaser and the item
    is dated in the window. Counts and links, never texts (the API's content is protected). The API allows 60
    calls per hour: callers keep to a handful of names per run."""
    url = "https://www.tagesschau.de/api2u/search/?" + urllib.parse.urlencode({"searchText": name, "pageSize": 30})
    key = hashlib.sha1(f"{name}|{end}".encode()).hexdigest()[:16]
    data = fetch_json(url, cache / "tagesschau" / f"{key}.json", sleep=1.0)
    if data is None:
        return None
    hits = []
    for item in data.get("searchResults", []):
        date = (item.get("date") or "")[:10]
        text = f"{item.get('title', '')} {item.get('topline', '')} {item.get('firstSentence', '')}"
        if start <= date <= end and name in text and item.get("shareURL"):
            hits.append({"title": item.get("title", ""), "date": date, "url": item["shareURL"]})
    return {"total": data.get("totalItemCount"), "hits": hits}


# ---------------------------------------------------------------- the people list


def people_payload(conn: sqlite3.Connection, date: str, fetch: bool = True, cache: Path = CACHE,
                   with_tagesschau: bool = False) -> dict:  # fmt: skip
    """The people part of the payload for the week ending on `date`."""
    start, end, base_start, base_end = window(date)
    persons = people(conn, start, end)
    week_days, base_days = days(start, end), days(base_start, base_end)
    titles = wiki_titles([p["qid"] for p in persons.values() if p["qid"]], cache) if fetch else {}
    views: dict[str, dict[str, int]] = {}
    if fetch:
        for n, p in enumerate(sorted(persons.values(), key=lambda p: p["id"])):
            if title := titles.get(p["qid"] or ""):
                p["wiki"] = title
                views[p["id"]] = pageviews(title, base_start, end, cache)
            if n % 100 == 99:
                print(f"  pageviews: {n + 1} of {len(persons)}")
    week_pl, house_rate = plenary(conn, start, end)
    base_pl, base_house = plenary(conn, base_start, base_end)
    base_weeks = sitting_weeks(conn, base_start, base_end)
    devs = deviations(conn, start, end)
    variants, ambiguous = name_variants(conn, persons)
    hib_week = hib_texts(conn, start, end)
    hib_base = hib_texts(conn, base_start, base_end)
    firsts = {f for p in persons.values() for f in (p["first"] or "").split()}
    m_week = mentions([(r["id"], f"{r['title']}\n{r['text']}") for r in hib_week], variants, firsts)
    m_base = mentions([(r["id"], f"{r['title']}\n{r['text']}") for r in hib_base], variants, firsts)
    hib_by_id = {r["id"]: r for r in hib_week}
    hib_base_weeks = max(1, len({week_id(r["date"]) for r in hib_base}))

    empty = {"reden": [], "words": 0, "zurufe": 0, "zf_asked": [], "zf_received": [], "answers": []}
    out = []
    for pid, p in persons.items():
        w, b = week_pl.get(pid, empty), base_pl.get(pid, empty)
        signals: dict[str, Signal] = {}
        if pid in views and (s := pageview_signal(views[pid], week_days, base_days)):
            signals["wikipedia"] = s
        signals["reden"] = count_signal("reden", len(w["reden"]), len(b["reden"]), base_weeks)
        signals["zurufe"] = rate_signal(w["zurufe"], w["words"], b["zurufe"], b["words"], base_house)
        zf_w = len(w["zf_asked"]) + len(w["zf_received"])
        zf_b = len(b["zf_asked"]) + len(b["zf_received"])
        signals["zwischenfragen"] = count_signal(
            "zwischenfragen", zf_w, zf_b, base_weeks, {"asked": len(w["zf_asked"]), "received": len(w["zf_received"])}
        )
        ans_w, ans_b = sum(a["n"] for a in w["answers"]), sum(a["n"] for a in b["answers"])
        signals["antworten"] = count_signal("antworten", ans_w, ans_b, base_weeks)
        hib_ids = sorted(m_week.get(pid, set()))
        signals["hib"] = count_signal("hib", len(hib_ids), len(m_base.get(pid, ())), hib_base_weeks)
        connected = bool(w["reden"] or zf_w or ans_w or hib_ids or devs.get(pid))
        group = include(signals, connected)
        if group is None:
            continue
        out.append({
            "id": pid, "name": p["name"], "fraction": p["fraction"] or p.get("party"), "role": p["role"],
            "url": f"{pid}.html", "group": group, "wiki": p.get("wiki"),
            "signals": {k: s.as_dict() for k, s in signals.items() if s.above or k == "wikipedia"
                        or (k in ("reden", "zwischenfragen", "antworten", "hib") and s.week)
                        or (k == "zurufe" and w["words"])},
            "above": [k for k, s in signals.items() if s.above],
            "evidence": {
                "reden": w["reden"][:MAX_EVIDENCE], "n_reden": len(w["reden"]),
                "zwischenfragen": (w["zf_asked"] + w["zf_received"])[:MAX_EVIDENCE],
                "antworten": w["answers"],
                "hib": [{"id": h, "date": hib_by_id[h]["date"], "title": hib_by_id[h]["title"],
                         "url": hib_by_id[h]["source_url"]} for h in hib_ids][:MAX_EVIDENCE],
                "abweichungen": devs.get(pid, []),
            },
        })  # fmt: skip
    for e in out:  # what the news was: the reader should not have to guess (a handful of calls)
        if e["group"] == "nachricht" and fetch:
            next_day = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()  # the morning's coverage
            e["tagesschau"] = tagesschau(e["name"], start, next_day, cache)
    if with_tagesschau:
        for e in [e for e in out if "tagesschau" not in e][:30]:  # 60 calls an hour: at most half in one run
            e["tagesschau"] = tagesschau(e["name"], start, end, cache)
    order = {"nachricht": 0, "beides": 1, "plenum": 2, "wikipedia": 3}
    out.sort(key=lambda e: (order[e["group"]], persons[e["id"]]["last"], e["name"]))
    return {"people": out, "n_persons": len(persons), "n_with_wiki": len(views),
            "pageviews_until": max((d for v in views.values() for d in v if d <= end), default=None),
            "sitting_weeks_in_baseline": base_weeks, "ambiguous_names": ambiguous,
            "hib_items_week": len(hib_week), "sittings": [r[0] for r in conn.execute(
                "SELECT date FROM sitting WHERE date BETWEEN ? AND ? ORDER BY date", (start, end))]}  # fmt: skip


# ---------------------------------------------------------------- topics


def subject_shares(conn: sqlite3.Connection, date: str) -> list[dict]:
    """Each Sachgebiet's share of the Bundestag's new Drucksachen in the 28 days up to `date` against its share
    in the 182 days before (+2 smoothing on counts and totals). A Drucksache counts once per Sachgebiet of its
    Vorgänge. Shares, not counts: the summer break halves every count. Rising Sachgebiete with at least
    TOPIC_FLOOR Drucksachen, steepest first, each with the newest Vorgänge."""
    end = dt.date.fromisoformat(date)
    recent_start = (end - dt.timedelta(days=TOPIC_RECENT_DAYS - 1)).isoformat()
    base_start = (end - dt.timedelta(days=TOPIC_RECENT_DAYS + TOPIC_BASE_DAYS - 1)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT d.id, d.date, s.value subject FROM drucksache d "
        "JOIN vorgang_drucksache vd ON vd.drucksache_id = d.id JOIN vorgang v ON v.id = vd.vorgang_id, "
        "json_each(v.subjects) s WHERE d.wahlperiode = 21 AND coalesce(d.publisher, 'BT') = 'BT' "
        "AND d.date BETWEEN ? AND ?",
        (base_start, date),
    ).fetchall()
    recent = Counter(r["subject"] for r in rows if r["date"] >= recent_start)
    base = Counter(r["subject"] for r in rows if r["date"] < recent_start)
    return rising(recent, base, conn, recent_start, date)


def share_ratio(r: int, r_total: int, b: int, b_total: int) -> float:
    return ((r + TOPIC_SMOOTH) / (r_total + TOPIC_SMOOTH)) / ((b + TOPIC_SMOOTH) / (b_total + TOPIC_SMOOTH))


def rising(recent: Counter, base: Counter, conn: sqlite3.Connection | None, start: str, end: str) -> list[dict]:
    rt, bt = sum(recent.values()), sum(base.values())
    out = []
    for subject, r in recent.items():
        ratio = share_ratio(r, rt, base.get(subject, 0), bt)
        if r < TOPIC_FLOOR or ratio < TOPIC_FACTOR:
            continue
        out.append({"subject": subject, "recent": r, "base": base.get(subject, 0), "ratio": round(ratio, 2),
                    "share": round(r / rt * 100, 1), "base_share": round(base.get(subject, 0) / bt * 100, 1),
                    "url": f"sachgebiete/{slug(subject)}.html",
                    "vorgaenge": newest_vorgaenge(conn, subject, start, end) if conn else []})  # fmt: skip
    return sorted(out, key=lambda t: -t["ratio"])


def newest_vorgaenge(conn: sqlite3.Connection, subject: str, start: str, end: str, n: int = 3) -> list[dict]:
    """The Vorgänge of a Sachgebiet with the most new Drucksachen in the window, as examples."""
    rows = conn.execute(
        "SELECT v.id, v.title, v.type, count(DISTINCT d.id) n, max(d.date) last FROM vorgang v "
        "JOIN vorgang_drucksache vd ON vd.vorgang_id = v.id JOIN drucksache d ON d.id = vd.drucksache_id "
        "WHERE d.date BETWEEN ? AND ? AND coalesce(d.publisher, 'BT') = 'BT' AND EXISTS "
        "(SELECT 1 FROM json_each(v.subjects) WHERE value = ?) GROUP BY v.id ORDER BY n DESC, last DESC LIMIT ?",
        (start, end, subject, n),
    )
    pages = {"Kleine Anfrage": "regierung/anfragen", "Große Anfrage": "regierung/anfragen"}
    return [{"id": r["id"], "title": r["title"], "type": r["type"],
             "url": f"{pages.get(r['type'] or '', 'vorgaenge')}/{r['id']}.html"} for r in rows]  # fmt: skip


_VERLANGEN = re.compile(r"auf Verlangen der (.+)", re.IGNORECASE)


def aktuelle_stunden(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """The week's Aktuelle Stunden: topic and the Fraktion(en) that asked for it ("auf Verlangen der …")."""
    out = []
    for r in conn.execute(
        "SELECT a.id, a.sitting_id, a.position, a.title, st.date FROM agenda_item a JOIN sitting st "
        "ON st.id = a.sitting_id WHERE a.kind = 'aktuelle_stunde' AND st.date BETWEEN ? AND ? ORDER BY st.date, "
        "a.position",
        (start, end),
    ):
        parts = [p.strip() for p in (r["title"] or "").split("|") if p.strip() and not p.strip().startswith("(")]
        asked = next((p for p in parts if _VERLANGEN.search(p)), "")
        topic = next((p for p in parts if p != asked and not p.startswith("Aktuelle Stunde")), "")
        out.append({"date": r["date"], "topic": topic, "requested_by": requesters(asked),
                    "url": f"sitzungen/{page_id(r['sitting_id'])}.html#top-{r['position']}",
                    "speeches": conn.execute("SELECT count(*) FROM speech WHERE agenda_item_id = ?",
                                             (r["id"],)).fetchone()[0]})  # fmt: skip
    return out


def requesters(text: str) -> list[str]:
    """ "auf Verlangen der Fraktionen der CDU/CSU und SPD" -> ["CDU/CSU", "SPD"]."""
    m = _VERLANGEN.search(text)
    if not m:
        return []
    rest = re.sub(r"^Fraktion(en)?\s+(der\s+)?", "", m.group(1).strip())
    return [fraction_name(p) for p in re.split(r"\s+und\s+|,\s*", rest) if p]


def befragungen(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """The week's Regierungsbefragung: who answered (with the department as the subject of the opening
    statement), how many questions each Fraktion asked; the Fragestunde's answering offices."""
    out = []
    for s in conn.execute(
        "SELECT DISTINCT st.id, st.date FROM sitting st JOIN agenda_item a ON a.sitting_id = st.id "
        "WHERE a.kind = 'befragung' AND st.date BETWEEN ? AND ? ORDER BY st.date",
        (start, end),
    ):
        turns = conn.execute(
            "SELECT q.role, s.person_id, s.speaker_name, s.speaker_role, coalesce(s.member_fraction, s.fraction) f "
            "FROM question_turn q JOIN speech s ON s.id = q.speech_id JOIN agenda_item a ON a.id = s.agenda_item_id "
            "WHERE a.kind = 'befragung' AND s.sitting_id = ? ORDER BY s.position",
            (s["id"],),
        ).fetchall()
        answering: dict[str, dict] = {}
        for t in turns:
            if t["role"] in ("einleitung", "antwort"):
                a = answering.setdefault(t["person_id"], {"id": t["person_id"], "url": f"{t['person_id']}.html",
                                                          "name": _TITLES.sub("", t["speaker_name"].split(",")[0]),
                                                          "office": t["speaker_role"], "answers": 0})  # fmt: skip
                a["answers"] += t["role"] == "antwort"
        asked = Counter(short(t["f"]) for t in turns if t["role"] in ("frage", "nachfrage", "zusatzfrage") and t["f"])
        url = f"regierung/regierungsbefragung/{page_id(s['id'])}.html"
        out.append({"date": s["date"], "sitting": s["id"], "url": url, "answering": list(answering.values()),
                    "questions_by_fraction": dict(asked.most_common())})  # fmt: skip
    return out


def topics_payload(conn: sqlite3.Connection, date: str) -> dict:
    start, end, _, _ = window(date)
    return {"sachgebiete": subject_shares(conn, date), "aktuelle_stunden": aktuelle_stunden(conn, start, end),
            "befragungen": befragungen(conn, start, end),
            "sachgebiete_window": {"recent_days": TOPIC_RECENT_DAYS, "base_days": TOPIC_BASE_DAYS}}  # fmt: skip


def build(conn: sqlite3.Connection, date: str, fetch: bool = True, cache: Path = CACHE,
          with_tagesschau: bool = False) -> dict:  # fmt: skip
    start, end, base_start, base_end = window(date)
    return {"start": start, "end": end, "baseline": [base_start, base_end], "week": week_id(end),
            **people_payload(conn, date, fetch, cache, with_tagesschau), "topics": topics_payload(conn, date),
            "method": METHOD}  # fmt: skip


def write(out: Path, payload: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / PREVIEW).write_text(preview_page(payload), encoding="utf-8")
    return out / FILE


# ---------------------------------------------------------------- rendering

METHOD = (
    "„Im Gespräch“ zeigt, wer und was in dieser Woche mehr Aufmerksamkeit bekommt als sonst. Jede Zahl wird mit "
    "derselben Person oder demselben Sachgebiet in den 90 Tagen davor verglichen, nicht mit anderen. Eine Person "
    "steht hier, wenn mindestens zwei Werte deutlich über ihrem Normalwert liegen oder wenn ihre Wikipedia-Seite "
    "ein Vielfaches der üblichen Aufrufe hat und sie in der Woche im Bundestag aufgetreten ist. Bei mehr als dem "
    "Zwanzigfachen steht sie auch ohne Auftritt hier: dann gibt es Nachrichten über sie, die tagesschau-Meldungen "
    "nennen sie. Gezählt werden "
    "Aufrufe der Wikipedia-Seite, Reden, Zurufe während der Reden je 1.000 Wörter, Zwischenfragen, Antworten in "
    "Regierungsbefragung und Fragestunde und Nennungen in hib-Meldungen. Die Reihenfolge ist keine Rangfolge. "
    "Aufmerksamkeit ist keine Bewertung. Ämter wie Ministerium oder Fraktionsvorsitz bringen mehr Aufmerksamkeit."
)

GROUPS = {
    "nachricht": "In den Nachrichten: ein Vielfaches der üblichen Aufrufe",
    "beides": "Mehr Aufrufe und mehr Aufmerksamkeit im Bundestag",
    "plenum": "Mehr Aufmerksamkeit im Bundestag",
    "wikipedia": "Viel mehr Aufrufe, in der Woche im Bundestag aufgetreten",
}


def num(x: float) -> str:
    """German thousands separator: 12345 -> "12.345"."""
    return f"{round(x):,}".replace(",", ".")


def approx(x: float) -> str:
    """A usual value, rounded to what it can carry: 0.4 -> "unter 1", 3.6 -> "4", 8,734 -> "8.700"."""
    if x < 1:
        return "unter 1" if x > 0 else "0"
    if x >= 1000:
        return num(round(x, -2))
    return num(x)


def signal_lines(e: dict) -> list[tuple[str, bool]]:
    """(text, above) per signal of a person, for the card."""
    s, lines = e["signals"], []
    if w := s.get("wikipedia"):
        lines.append((f"Aufrufe der Wikipedia-Seite: {num(w['week'])} (sonst ~{approx(w['base'])} pro Woche)",
                      w["above"]))  # fmt: skip
    if r := s.get("reden"):
        lines.append((f"{num(r['week'])} {'Rede' if r['week'] == 1 else 'Reden'} im Plenum "
                      f"(sonst ~{approx(r['base'])} pro Sitzungswoche)", r["above"]))  # fmt: skip
    if z := s.get("zurufe"):
        lines.append((f"{num(z['events'])} Zurufe, Lachen oder Widerspruch während der Reden, "
                      f"{z['week']:.0f} je 1.000 Wörter (sonst ~{z['base']:.0f})", z["above"]))  # fmt: skip
    if q := s.get("zwischenfragen"):
        parts = [f"{q['asked']} gestellt" if q["asked"] else "", f"{q['received']} erhalten" if q["received"] else ""]
        lines.append((f"Zwischenfragen: {', '.join(p for p in parts if p)} "
                      f"(sonst ~{approx(q['base'])} pro Sitzungswoche)", q["above"]))  # fmt: skip
    if a := s.get("antworten"):
        lines.append((f"{num(a['week'])} Antworten in Regierungsbefragung und Fragestunde "
                      f"(sonst ~{approx(a['base'])} pro Sitzungswoche)", a["above"]))  # fmt: skip
    if h := s.get("hib"):
        lines.append((f"In {num(h['week'])} hib-{'Meldung' if h['week'] == 1 else 'Meldungen'} genannt "
                      f"(sonst ~{approx(h['base'])} pro Woche)", h["above"]))  # fmt: skip
    return lines


def _a(href: str, label: str, research: str) -> str:
    url = href if href.startswith("http") else research + href
    return f'<a href="{html.escape(url)}">{html.escape(label)}</a>'


def render_person(e: dict, research: str) -> str:
    who = html.escape(short(e["fraction"]) if e["fraction"] else "")
    role = f'<div class="gs-role">{html.escape(e["role"])}</div>' if e["role"] else ""
    lines = "".join(f'<li class="{"gs-up" if up else ""}">{html.escape(t)}</li>' for t, up in signal_lines(e))
    ev = e["evidence"]
    links = []
    for r in ev["reden"]:
        links.append(_a(r["url"], f"Rede {de_date(r['date'])}: {r['top']}", research))
    for z in ev["zwischenfragen"][:3]:
        links.append(_a(z["url"], f"Zwischenfrage {de_date(z['date'])}", research))
    for a in ev["antworten"]:
        if a.get("url"):
            links.append(_a(a["url"], f"Regierungsbefragung {a['sitting']}", research))
    for h in ev["hib"]:
        links.append(_a(h["url"], f"hib {de_date(h['date'])}: {h['title']}", research))
    for d in ev["abweichungen"]:
        links.append(_a(d["url"], f"Gegen die eigene Fraktion gestimmt: {d['title']}", research))
    for t in (e.get("tagesschau") or {}).get("hits", [])[:3]:
        links.append(_a(t["url"], f"tagesschau {de_date(t['date'])}: {t['title']}", research))
    if e.get("wiki"):
        links.append(_a(f"https://de.wikipedia.org/wiki/{urllib.parse.quote(e['wiki'].replace(' ', '_'))}",
                        "Wikipedia-Artikel", research))  # fmt: skip
    belege = (f'<details class="gs-ev"><summary>Belege</summary><ul>{"".join(f"<li>{x}</li>" for x in links)}'
              f"</ul></details>") if links else ""  # fmt: skip
    return (f'<li class="gs-person"><div class="gs-name">{_a(e["url"], e["name"], research)} '
            f'<span class="gs-frac">{who}</span></div>{role}<ul class="gs-sig">{lines}</ul>{belege}</li>')  # fmt: skip


def render_topics(t: dict, research: str) -> str:
    parts = []
    if t["aktuelle_stunden"]:
        items = "".join(
            f"<li>{_a(a['url'], a['topic'], research)} <span class='gs-meta'>{de_date(a['date'])}, verlangt von "
            f"{html.escape(' und '.join(short(f) for f in a['requested_by']) or 'unbekannt')}</span></li>"
            for a in t["aktuelle_stunden"]
        )
        parts.append(f"<h3>Aktuelle Stunden</h3><ul class='gs-list'>{items}</ul>")
    if t["befragungen"]:
        items = []
        for b in t["befragungen"]:
            who = "; ".join(f"{_a(a['url'], a['name'], research)} ({html.escape(a['office'] or '')}, "
                            f"{a['answers']} Antworten)" for a in b["answering"])  # fmt: skip
            asked = ", ".join(f"{html.escape(f)} {n}" for f, n in b["questions_by_fraction"].items())
            items.append(f"<li>{_a(b['url'], 'Regierungsbefragung ' + de_date(b['date']), research)}: {who}"
                         f"<div class='gs-meta'>Fragen nach Fraktion: {asked}</div></li>")  # fmt: skip
        parts.append(f"<h3>Regierungsbefragung</h3><ul class='gs-list'>{''.join(items)}</ul>")
    if t["sachgebiete"]:
        w = t["sachgebiete_window"]
        items = []
        for s in t["sachgebiete"][:8]:
            ex = "".join(f"<li>{_a(v['url'], v['title'], research)}</li>" for v in s["vorgaenge"])
            items.append(
                f"<li>{_a(s['url'], s['subject'], research)} <span class='gs-meta'>{s['share']:.0f} % der neuen "
                f"Drucksachen ({s['recent']}), sonst {s['base_share']:.0f} %</span>"
                f"<details class='gs-ev'><summary>Beispiele</summary><ul>{ex}</ul></details></li>"
            )
        expl = (f"Anteil an den neuen Drucksachen der letzten {w['recent_days']} Tage, verglichen mit den "
                f"{w['base_days']} Tagen davor.")  # fmt: skip
        parts.append(f"<h3>Sachgebiete mit mehr Drucksachen als sonst{shell.info(expl)}</h3>"
                     f"<ul class='gs-list'>{''.join(items)}</ul>")  # fmt: skip
    return "".join(parts)


STYLE = f"""
.gs {{ --gs: {RADAR}; }}
.gs h2 {{ margin: 0 0 .2rem; }}
.gs .gs-tag {{ color: var(--gs); font-size: .8rem; font-weight: 600; letter-spacing: .03em;
  text-transform: uppercase; }}
.gs h3 {{ font-size: 1rem; margin: 1.2rem 0 .4rem; border-bottom: 2px solid var(--gs); padding-bottom: .15rem; }}
.gs ul.gs-people, .gs ul.gs-list {{ list-style: none; padding: 0; margin: 0; }}
.gs .gs-person, .gs ul.gs-list > li {{ padding: .55rem 0; border-bottom: 1px solid #e5e7eb; }}
.gs .gs-name {{ font-weight: 600; }}
.gs .gs-frac, .gs .gs-meta {{ color: #6b7280; font-size: .85rem; font-weight: 400; }}
.gs .gs-role {{ font-size: .85rem; color: #4b5563; }}
.gs ul.gs-sig {{ margin: .25rem 0 0; padding-left: 1.1rem; font-size: .9rem; }}
.gs ul.gs-sig li.gs-up {{ font-weight: 600; }}
.gs ul.gs-sig li.gs-up::marker {{ color: var(--gs); }}
.gs details.gs-ev {{ font-size: .85rem; margin-top: .2rem; }}
.gs details.gs-ev summary {{ cursor: pointer; color: var(--gs); }}
.gs details.gs-ev ul {{ padding-left: 1.1rem; margin: .2rem 0; }}
.gs .gs-cols {{ display: grid; gap: 0 2rem; }}
@media (min-width: 900px) {{ .gs .gs-cols {{ grid-template-columns: 3fr 2fr; }} }}
.gs .gs-method {{ font-size: .8rem; color: #6b7280; }}
"""


def render_section(payload: dict, research: str | None = None) -> str:
    """The section as an HTML fragment (its own <style>, no script): people grouped by their evidence, then
    topics. Links point into Research."""
    from radar.build import RESEARCH

    research = research or RESEARCH
    groups = []
    for key, label in GROUPS.items():
        members = [e for e in payload["people"] if e["group"] == key]
        if members:
            groups.append(f"<h3>{html.escape(label)}</h3><ul class='gs-people'>"
                          f"{''.join(render_person(e, research) for e in members)}</ul>")  # fmt: skip
    if not groups:
        groups.append("<p>In dieser Woche liegt bei keiner Person mehr als ein Wert deutlich über dem Normalwert.</p>")
    pv = payload.get("pageviews_until")
    note = (f"Wikipedia-Aufrufe bis {de_date(pv)}. " if pv else "") + (
        f"Sitzungen: {', '.join(de_date(d) for d in payload['sittings'])}." if payload["sittings"]
        else "In dieser Woche hat der Bundestag nicht getagt.")  # fmt: skip
    return (
        f"<section class='gs' id='im-gespraech'><style>{STYLE}</style>"
        f"<div class='gs-tag'>Radar · Aufmerksamkeit</div>"
        f"<h2>Im Gespräch{shell.info(html.escape(payload['method']))}</h2>"
        f"<p class='gs-meta'>{de_date(payload['start'])} bis {de_date(payload['end'])}. {html.escape(note)}</p>"
        f"<div class='gs-cols'><div><h3 class='gs-h'>Personen</h3>{''.join(groups)}</div>"
        f"<div><h3 class='gs-h'>Themen</h3>{render_topics(payload['topics'], research)}</div></div></section>"
    )


def preview_page(payload: dict) -> str:
    from radar import shell
    from radar.build import RESEARCH

    return (
        "<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Im Gespräch (Labor)</title>{shell.head(RESEARCH)}"
        "<style>body{max-width:72rem;margin:0 auto;padding:0 1rem 3rem;font-family:system-ui,sans-serif;"
        "line-height:1.45}</style></head><body>"
        f"{shell.site_header(RESEARCH)}<main>{render_section(payload)}</main></body></html>"
    )


def log_summary(payload: dict) -> None:
    print(f"{payload['start']}..{payload['end']}: {len(payload['people'])} of {payload['n_persons']} persons "
          f"({payload['n_with_wiki']} with Wikipedia, views until {payload['pageviews_until']})")  # fmt: skip
    for e in payload["people"]:
        print(f"  [{e['group']:9}] {e['name']} ({short(e['fraction'])}{', ' + e['role'] if e['role'] else ''}): "
              f"{', '.join(e['above'])}")  # fmt: skip
    for s in payload["topics"]["sachgebiete"]:
        print(f"  Sachgebiet {s['subject']}: {s['share']} % ({s['recent']}) vs {s['base_share']} % ×{s['ratio']}")
