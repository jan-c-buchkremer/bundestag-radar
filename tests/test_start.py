from radar import start


def test_nothing_built_leaves_the_index_as_before(tmp_path):
    assert start.sections(tmp_path, "https://plenar-radar.de/") == ""


def test_start_page_keeps_the_news_and_a_few_others():
    people = [{"group": "beides", "n": i} for i in range(10)] + [{"group": "nachricht", "n": 99}]
    short = start.short_people({"people": people}, n=3)
    assert [e["n"] for e in short["people"]] == [99, 0, 1, 2]
