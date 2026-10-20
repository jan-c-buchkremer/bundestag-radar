from radar import shell, start, themes


def test_nothing_built_leaves_the_index_as_before(tmp_path):
    assert start.sections(tmp_path, "https://plenar-radar.de/") == {"lead": "", "stories": "", "rising": ""}


def test_start_page_keeps_the_news_and_a_few_others():
    people = [{"group": "beides", "n": i} for i in range(10)] + [{"group": "nachricht", "n": 99}]
    short = start.short_people({"people": people}, n=3)
    assert [e["n"] for e in short["people"]] == [99, 0, 1, 2]


def test_people_line_names_and_links_the_full_page():
    people = [{"group": "nachricht", "name": "A <b>"}, {"group": "plenum", "name": "B"}]
    line = start.people_line({"people": people})
    assert "A &lt;b&gt;, B" in line and 'href="gespraech.html"' in line and "alle 2 mit Zahlen" in line


def _source(share, mean, rising):
    return {"share": share, "mean": mean, "spread": 0.01, "baseline": [mean] * 8, "n": 6, "z": 3.0,
            "rising": rising, "docs": []}  # fmt: skip


def _theme(tid, label, sources):
    return {"id": tid, "label": label, "terms": ["a"], "area": "weitere", "thema_der_woche": sum(
        v["rising"] for v in sources.values()) >= 2, "rising_in": [k for k, v in sources.items() if v["rising"]],
        "sources": sources}  # fmt: skip


def test_rise_factor_counts_only_the_sources_a_theme_rises_in():
    t = _theme("T1", "x", {"reden": _source(0.06, 0.01, True), "hib": _source(0.0, 0.02, False),
                           "drucksachen": _source(0.03, 0.01, True)})  # fmt: skip
    assert themes.rise_factor(t) == 0.09 / 0.02
    t["sources"]["reden"]["mean"] = t["sources"]["drucksachen"]["mean"] = 0.0
    assert themes.rise_factor(t) is None and themes.factor_text(None) == "neu"


def test_rising_chart_puts_themes_of_the_week_first_and_hides_details_behind_a_click():
    one = _theme("T1", "Einzeln", {"reden": _source(0.2, 0.01, True), "hib": _source(0.01, 0.01, False),
                                   "drucksachen": _source(0.01, 0.01, False)})  # fmt: skip
    two = _theme("T2", "Doppelt", {"reden": _source(0.02, 0.01, True), "hib": _source(0.02, 0.01, True),
                                   "drucksachen": _source(0.01, 0.01, False)})  # fmt: skip
    quiet = _theme("T3", "Still", {s: _source(0.01, 0.01, False) for s in themes.SOURCES})
    payload = {"week": "2026-W41", "baseline_weeks": [f"2026-W{w}" for w in range(30, 38)],
               "themes": [one, two, quiet]}  # fmt: skip
    out = themes.render_rising(payload, "<p>people</p>")
    assert out.index("Doppelt") < out.index("Einzeln") and "Still" not in out
    assert "2,0×" in out and "20,0×" in out and "Thema der Woche" in out
    assert out.count("<li class=") == 2 and 'class="info"' in out and out.endswith("<p>people</p></section>")


def test_info_button_carries_its_text_as_tooltip():
    out = shell.info("Erklärt &amp; mehr")
    assert 'class="info-b"' in out and 'role="tooltip">Erklärt &amp; mehr<' in out
