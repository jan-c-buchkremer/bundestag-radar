"""landscape weeks | landscape build 2026-W28 [2026-W26 …] | landscape build overview | landscape build --all |
landscape serve"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from landscape import build, corpus, embed


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="landscape")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("weeks", help="list sitting weeks in the foundation store")
    b = sub.add_parser("build", help="build the map for one or more weeks")
    b.add_argument("weeks", nargs="*", help="ISO weeks, e.g. 2026-W28, and/or `overview` (period themes + index)")
    b.add_argument("--all", action="store_true", help="every week in the store, the period overview and the index")
    b.add_argument("--out", type=Path, default=Path("data/out"))
    b.add_argument("--store", type=Path, default=Path("data/landscape.sqlite"), help="embedding cache")
    b.add_argument("--min-cluster-size", type=int, default=8)
    b.add_argument("--period-min-cluster-size", type=int, default=None, help="HDBSCAN size for the period themes")
    sv = sub.add_parser("serve", help="dev server: pages are re-rendered from data/out/*.json on every request")
    sv.add_argument("--out", type=Path, default=Path("data/out"))
    sv.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)

    if args.cmd == "serve":
        from landscape.serve import serve

        serve(args.out, args.port)
        return

    conn = corpus.connect()
    all_weeks = corpus.weeks(conn)
    if args.cmd == "weeks":
        for w in all_weeks:
            first, last, n = w["sittings"][0], w["sittings"][-1], len(w["sittings"])
            print(f"{w['week']}  sittings {first}-{last} ({n})  speeches {w['speeches']}")
        return

    import time

    from landscape import period
    from landscape.cluster import cluster

    week_ids = [w["week"] for w in all_weeks]
    with_overview = args.all or "overview" in args.weeks
    targets = week_ids if args.all else [w for w in args.weeks if w != "overview"]
    if not targets and not with_overview:
        sys.exit("give at least one week, `overview` or --all; see `landscape weeks`")
    store = embed.open_store(args.store)
    args.out.mkdir(parents=True, exist_ok=True)
    summaries = []
    for week in targets:
        speeches = corpus.load_week(conn, week)
        if not speeches:
            print(f"{week}: no speeches, skipped")
            continue
        print(f"{week}: {len(speeches)} speeches after re-joining splits and dropping < {corpus.MIN_CHARS} chars")
        vectors = embed.embed_speeches(speeches, store)
        result = cluster(vectors, [s.text for s in speeches], args.min_cluster_size)
        decisions = corpus.decisions(conn, [s.agenda_item_id for s in speeches if s.agenda_item_id])
        payload = build.week_payload(week, speeches, result, vectors, week_ids, decisions)
        noise = sum(s["cluster"] < 0 for s in payload["speeches"])
        print(f"  {len(payload['clusters'])} clusters, {noise} unclustered speeches")
        for c in payload["clusters"]:
            print(f"  {c['id']:2} ({c['n']:3}) {', '.join(c['terms'])}")
        (args.out / f"{week}.html").write_text(build.render(payload), encoding="utf-8")
        (args.out / f"{week}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        summaries.append(build.summary(payload))
    if targets:
        print(f"wrote {len(summaries)} week page(s) to {args.out}")
    if with_overview:
        t0 = time.time()
        size = args.period_min_cluster_size or period.MIN_CLUSTER_SIZE
        model = period.build(conn, week_ids, store, args.out / "period.json", size)
        for t in model["themes"]:
            print(f"  {t['id']:2} ({t['n']:4}) {', '.join(t['terms'])}")
        (args.out / "index.html").write_text(build.index_page(args.out), encoding="utf-8")
        print(f"overview: {len(model['themes'])} themes, index written in {time.time() - t0:.0f} s")
    n = build.write_speech_clusters(args.out)
    print(f"speech_clusters.json: {n} speech ids")


if __name__ == "__main__":
    main()
