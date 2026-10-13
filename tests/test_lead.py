import json
import subprocess
from types import SimpleNamespace

from radar import lead, llm
from radar.lead import Source, check, parse
from radar.llm import AnthropicAPI, ClaudeCLI, Document

SOURCES = {
    "hib 787/2026": Source("hib 787/2026", "Die AfD-Fraktion fordert 24 Maßnahmen gegen Linksextremismus.", "u", "hib"),
    "Abstimmung 21/99/5": Source(
        "Abstimmung 21/99/5", "Mit Ja haben gestimmt 131, mit Nein 446. Der Gesetzentwurf ist abgelehnt.", "u", "A"
    ),
    "Rede ID1": Source("Rede ID1", "Anna Beispiel (SPD): Wir lehnen ab.", "u", "Rede", fraction="SPD"),
}


def test_parse_splits_sentences_cites_and_paragraphs():
    text = "Der Bundestag beriet. [Debatte 21/99/2]\nEs gab 131 Ja-Stimmen. [Abstimmung 21/99/5; hib 1/2026]\n\nNeu."
    out = parse(text)
    assert [s["text"] for s in out] == ["Der Bundestag beriet.", "Es gab 131 Ja-Stimmen.", "Neu."]
    assert out[1]["cites"] == ["Abstimmung 21/99/5", "hib 1/2026"]
    assert [s["paragraph"] for s in out] == [0, 0, 1]
    assert out[2]["cites"] == []


def test_check_passes_a_sentence_backed_by_its_source():
    s = {"text": "Es gab 131 Ja- und 446 Nein-Stimmen.", "cites": ["Abstimmung 21/99/5"]}
    assert check(s, SOURCES, set()) == []


def test_check_fails_missing_unknown_and_unbacked():
    assert check({"text": "Etwas.", "cites": []}, SOURCES, set()) == ["keine Quelle genannt"]
    assert any("unbekannte Quelle" in f for f in check({"text": "X.", "cites": ["hib 9/2026"]}, SOURCES, set()))
    fails = check({"text": "Die AfD nennt 25 Maßnahmen.", "cites": ["hib 787/2026"]}, SOURCES, set())
    assert fails == ["Zahl 25 nicht in den genannten Quellen"]


def test_check_finds_a_person_not_in_the_sources():
    s = {"text": "Friedrich Merz fordert 24 Maßnahmen.", "cites": ["hib 787/2026"]}
    assert check(s, SOURCES, {("Friedrich", "Merz")}) == ["Name Friedrich Merz nicht in den genannten Quellen"]


def test_position_sentence_must_cite_its_own_fraction():
    s = {"text": "AfD: Die AfD lehnt den Antrag ab.", "cites": ["Rede ID1"]}
    assert "keine Rede der Fraktion AfD genannt" in check(s, SOURCES, set(), fraction="AfD")
    assert check({**s, "text": "SPD: Die SPD lehnt den Antrag ab."}, SOURCES, set(), fraction="SPD") == []


def test_cache_returns_the_stored_result(tmp_path):
    calls = []

    class Fake:
        name, model = "fake", "m"

        def generate(self, system, documents, task):
            calls.append(task)
            return llm.Result(text="T. [hib 787/2026]", model="m", backend="fake")

    cache = lead.Cache(tmp_path)
    docs = [Document("hib 787/2026", "x")]
    first, hit1 = lead._call(Fake(), cache, docs, "task")
    second, hit2 = lead._call(Fake(), cache, docs, "task")
    assert (hit1, hit2) == (False, True) and first == second and calls == ["task"]


def test_cli_backend_sends_sources_on_stdin_and_reads_json():
    seen = {}

    def runner(args, input, **kw):
        seen.update(args=args, input=input, cwd=kw["cwd"])
        out = {"result": "Satz. [hib 1/2026]", "modelUsage": {"claude-x": {}}, "total_cost_usd": 0.01}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(out), stderr="")

    r = ClaudeCLI(model="sonnet", runner=runner).generate("SYS", [Document("hib 1/2026", "Text")], "Aufgabe")
    assert r.text == "Satz. [hib 1/2026]" and r.model == "claude-x" and r.cost_usd == 0.01
    assert '<quelle id="hib 1/2026">' in seen["input"] and seen["input"].endswith("Aufgabe")
    assert seen["args"][seen["args"].index("--system-prompt") + 1] == "SYS"
    assert "bundestag" not in seen["cwd"]  # no project CLAUDE.md in the prompt


def test_api_backend_builds_document_blocks_and_turns_citations_into_ids():
    docs = [Document("hib 1/2026", "a"), Document("Drucksache 21/1", "b")]
    message = SimpleNamespace(
        stop_reason="end_turn",
        model="claude-test",
        usage={"input_tokens": 1},
        content=[
            SimpleNamespace(type="text", text="Erster Satz.", citations=[SimpleNamespace(document_index=1)]),
            SimpleNamespace(type="text", text="\n", citations=None),
        ],
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **body: message),
        beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **body: message)),
    )
    api = AnthropicAPI(model="claude-test", client=client)
    body = api.request("SYS", docs, "Aufgabe")
    assert [b["title"] for b in body["messages"][0]["content"][:2]] == ["hib 1/2026", "Drucksache 21/1"]
    assert body["messages"][0]["content"][-1] == {"type": "text", "text": "Aufgabe"}
    assert api.generate("SYS", docs, "Aufgabe").text == "Erster Satz. [Drucksache 21/1]"
