import json

import pytest

from landscape import mentions
from landscape.mentions import Gazetteer, count_mentions, load_gazetteer, open_store, tag_texts


@pytest.fixture(scope="module")
def gaz() -> Gazetteer:
    return load_gazetteer()


def test_countries_laender_organisations_link_to_qids(gaz):
    assert gaz.link("Deutschland", "LOC") == "Q183"
    assert gaz.link("Ukraine", "ORG") == "Q212"
    assert gaz.link("Bayern", "LOC") == "Q980"
    assert gaz.link("NATO", "ORG") == "Q7184"
    assert gaz.link("EZB", "ORG") == "Q8901"
    assert gaz.link("USA", "LOC") == "Q30"
    assert gaz.link("China", "LOC") == "Q148"


def test_genitive_is_linked(gaz):
    assert gaz.link("Berlins", "LOC") == "Q64"
    assert gaz.link("Deutschlands", "LOC") == "Q183"


def test_denylisted_names_are_never_linked(gaz):
    for name in ("Essen", "Halle", "Hagen", "Hof", "Hamm"):
        assert gaz.link(name, "LOC") is None
    assert gaz.link("Halle (Saale)", "LOC") == "Q2814"  # the full name is unambiguous


def test_gemeinden_need_a_place_tag_but_others_do_not(gaz):
    assert gaz.link("Kiel", "LOC") == "Q1707"
    assert gaz.link("Kiel", "ORG") is None
    assert gaz.link("Bundeswehr", "ORG") == "Q56010"


def test_persons_and_unknown_names_are_not_linked(gaz):
    assert gaz.link("Olaf Scholz", "PER") is None
    assert gaz.link("Deutschland", "PER") is None
    assert gaz.link("Kleinkleckersdorf", "LOC") is None


def test_ambiguous_short_forms_are_dropped_unless_reviewed(gaz):
    assert gaz.link("Offenbach", "LOC")  # short form of "Offenbach am Main", unique
    assert gaz.link("Frankfurt", "LOC") == "Q1794"  # two Frankfurts: settled in aliases.tsv
    assert gaz.link("Frankfurt (Oder)", "LOC") == "Q4024"


def test_shared_name_goes_to_the_land(gaz):
    assert gaz.link("Bremen", "LOC") == "Q1209"
    assert gaz.link("Hamburg", "LOC") == "Q1055"


def test_gazetteer_qids_are_wellformed(gaz):
    assert all(q.startswith("Q") and q[1:].isdigit() for q in gaz.entities)
    assert {e["kind"] for e in gaz.entities.values()} == {"land", "staat", "organisation", "gemeinde"}


def test_short_forms():
    assert mentions.short_forms("Offenbach am Main") == ["Offenbach"]
    assert mentions.short_forms("Halle (Saale)") == ["Halle"]
    assert mentions.short_forms("Rostock") == []


def test_tag_texts_caches_by_text_hash(tmp_path):
    store = open_store(tmp_path / "s.sqlite")
    calls = []

    def loader():
        def tagger(texts):
            calls.append(list(texts))
            return [[("Berlin", "LOC")] for _ in texts]

        return tagger

    texts = {"a": "Berlin ist groß.", "b": "Berlin ist groß.", "c": "Anders."}
    first = tag_texts(texts, store, loader=loader)
    assert first["a"] == first["b"] == [("Berlin", "LOC")]
    assert sum(len(c) for c in calls) == 2  # identical texts are tagged once
    tag_texts(texts, store, loader=loader)  # served from the cache: the model is not even loaded
    assert len(calls) == 1


def test_count_and_payload(gaz):
    spans = {"s1": [("Berlin", "LOC"), ("Berlin", "LOC"), ("Essen", "LOC"), ("NATO", "ORG")], "s2": [("Essen", "LOC")]}
    counted = count_mentions(spans, gaz)
    assert counted == {"s1": {"Q64": 2, "Q7184": 1}}
    payload = mentions.build_payload(counted, gaz)
    assert payload["entities"]["Q64"] == {"label": "Berlin", "kind": "land"}
    json.dumps(payload)
