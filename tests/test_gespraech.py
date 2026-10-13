from collections import Counter

from radar import gespraech as g

WEEK = g.days("2026-10-06", "2026-10-12")
BASE = g.days("2026-07-08", "2026-10-05")


def views(base: int, week: list[int]) -> dict[str, int]:
    return {d: base for d in BASE} | dict(zip(WEEK, week, strict=True))


def test_pageviews_against_own_median():
    s = g.pageview_signal(views(100, [100] * 7), WEEK, BASE)
    assert s is not None and not s.above and not s.extreme
    s = g.pageview_signal(views(100, [300] * 7), WEEK, BASE)
    assert s.above and not s.extreme  # three times the usual week, 1,400 more readers
    s = g.pageview_signal(views(500, [500] * 6 + [300000]), WEEK, BASE)
    assert s.extreme and s.detail["news"] and s.detail["peak"] == "2026-10-12"


def test_pageviews_need_a_baseline():
    assert g.pageview_signal({d: 100 for d in WEEK}, WEEK, BASE) is None


def test_one_viral_baseline_day_does_not_hide_the_week():
    daily = views(100, [400] * 7)
    daily[BASE[10]] = 100000
    assert g.pageview_signal(daily, WEEK, BASE).above


def test_counts_need_a_minimum_and_a_factor():
    assert not g.count_signal("reden", 1, 0, 10).above  # 0 to 1 is no event
    assert g.count_signal("reden", 4, 10, 10).above  # 4 against 1 per sitting week
    assert not g.count_signal("reden", 4, 40, 10).above  # 4 is usual for this person


def test_include_groups():
    up = g.Signal("reden", 4, 1, True)
    wiki = g.Signal("wikipedia", 5000, 700, True, extreme=True)
    news = g.Signal("wikipedia", 300000, 3500, True, extreme=True, detail={"news": True})
    assert g.include({"wikipedia": news}, connected=False) == "nachricht"
    assert g.include({"wikipedia": wiki, "reden": up}, connected=True) == "beides"
    assert g.include({"reden": up, "zurufe": g.Signal("zurufe", 9, 3, True)}, connected=True) == "plenum"
    assert g.include({"wikipedia": wiki}, connected=True) == "wikipedia"
    assert g.include({"wikipedia": wiki}, connected=False) is None  # attention without the Bundestag
    assert g.include({"reden": up}, connected=True) is None  # one signal is not enough


def test_mentions_need_the_full_name_on_word_boundaries():
    variants = {"Bärbel Bas": "p1", "Christian Schmidt": "p2"}
    texts = [
        ("h1", "Bundesministerin Bärbel Bas stellte den Bericht vor."),
        ("h2", "Die Bastelgruppe und Bärbel Bastian."),
        ("h3", "Hans Christian Schmidt sprach."),
        ("h4", "Christian Schmidt (CSU) sprach."),
    ]
    found = g.mentions(texts, variants, {"Hans", "Christian", "Bärbel"})
    assert found == {"p1": {"h1"}, "p2": {"h4"}}


def test_topic_shares_not_counts():
    # the summer break halves every count; only a Sachgebiet whose share grows rises
    recent = Counter({"Energie": 30, "Umwelt": 20, "Verkehr": 50})
    base = Counter({"Energie": 60, "Umwelt": 80, "Verkehr": 260})
    assert g.share_ratio(30, 100, 60, 400) > g.TOPIC_FACTOR
    assert g.share_ratio(50, 100, 260, 400) < 1
    names = [t["subject"] for t in g.rising(recent, base, None, "2026-09-15", "2026-10-12")]
    assert "Energie" in names and "Verkehr" not in names
