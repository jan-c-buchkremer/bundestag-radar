"""Refresh the place gazetteers from Wikidata (CC0): `uv run python scripts/fetch_gazetteers.py`.

Writes src/landscape/gazetteer/{countries,laender,gemeinden}.tsv (qid, German label[, population]).
The files are committed: builds never need the network. The organisation list is hand-made (organisations.tsv)."""

from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src/landscape/gazetteer"
ENDPOINT = "https://query.wikidata.org/sparql"
UA = "bundestag-topic-landscape/0.1 (https://github.com/jan-c-buchkremer/bundestag-topic-landscape)"
# not UN members, but named as countries in the Bundestag
EXTRA_COUNTRIES = [
    ["Q865", "Taiwan"],
    ["Q1246", "Kosovo"],
    ["Q219060", "Palästina"],
    ["Q237", "Vatikanstadt"],
    ["Q39760", "Gaza-Streifen"],
]
MIN_POPULATION = 50_000  # Gemeinden below this are not listed: too many names that are also common words

COUNTRIES = """
SELECT ?c ?l WHERE {
  ?c wdt:P31 wd:Q6256 . FILTER NOT EXISTS { ?c wdt:P576 ?end }   # country, not dissolved
  ?c wdt:P463 wd:Q1065 .                                           # member of the United Nations
  ?c rdfs:label ?l . FILTER(LANG(?l) = "de")
}"""
LAENDER = """
SELECT ?c ?l WHERE { ?c wdt:P31 wd:Q1221156 . ?c rdfs:label ?l . FILTER(LANG(?l) = "de") }"""
GEMEINDEN = """
SELECT ?c ?l (MAX(?p) AS ?pop) WHERE {
  ?c wdt:P31/wdt:P279* wd:Q262166 . FILTER NOT EXISTS { ?c wdt:P576 ?end }
  ?c wdt:P1082 ?p . FILTER(?p >= %d)
  ?c rdfs:label ?l . FILTER(LANG(?l) = "de")
} GROUP BY ?c ?l ORDER BY DESC(?pop)"""


def sparql(query: str) -> list[dict]:
    req = urllib.request.Request(
        ENDPOINT + "?" + urllib.parse.urlencode({"query": query, "format": "json"}), headers={"User-Agent": UA}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["results"]["bindings"]


def write(name: str, rows: list[list[str]], header: list[str]) -> None:
    with (OUT / name).open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(header)
        w.writerows(sorted(rows, key=lambda r: r[1]))
    print(f"{name}: {len(rows)} rows")


def qid(b: dict) -> str:
    return b["c"]["value"].rsplit("/", 1)[1]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write("countries.tsv", [[qid(b), b["l"]["value"]] for b in sparql(COUNTRIES)] + EXTRA_COUNTRIES,
          ["qid", "label"])  # fmt: skip
    write("laender.tsv", [[qid(b), b["l"]["value"]] for b in sparql(LAENDER)], ["qid", "label"])
    write("gemeinden.tsv", [[qid(b), b["l"]["value"], b["pop"]["value"]] for b in sparql(GEMEINDEN % MIN_POPULATION)],
          ["qid", "label", "population"])  # fmt: skip


if __name__ == "__main__":
    main()
