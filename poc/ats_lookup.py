#!/usr/bin/env python3
"""CLI wrapper around job_scout.ats - resolve LinkedIn alert entries to the
real company postings and check remote eligibility from Greece.

    python3 poc/ats_lookup.py poc/alerts.json [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from job_scout.ats import resolve  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="JSON list of {company, title, location}")
    ap.add_argument("--json", help="also write full results to this file")
    ap.add_argument("--min-score", type=float, default=0.55)
    args = ap.parse_args()

    entries = json.load(open(args.input, encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda e: resolve(e, args.min_score), entries))

    found = sum(r["status"] == "matched" for r in results)
    boards = sum(r["ats"] is not None for r in results)
    for r in results:
        head = f"{r['company']} | {r['title']}  (LinkedIn: {r.get('location', '-')})"
        print(head)
        if r["status"] == "matched":
            m = r["match"]
            remote = {True: "remote", False: "not remote", None: "remote ?"}[m["remote"]]
            print(f"   OK  [{r['ats']}:{r['slug']}] {m['title']}")
            el = r["eligibility"]
            print(f"       location: {m['location'] or '-'} | {remote} | score {r['score']}")
            print(f"       {m['url']}")
            if r.get("same_title_locations"):
                print(f"       also posted in: {', '.join(r['same_title_locations'])}")
            print(f"       ELIGIBILITY: {el['verdict']}")
            for ev in el["evidence"][:4]:
                print(f"         > {ev}")
        elif r["ats"]:
            c = r["closest"]
            print(f"   ??  [{r['ats']}:{r['slug']}] {r['board_size']} postings, closest: "
                  f"{c['title']} (score {r['score']})")
        else:
            print("   --  no Greenhouse/Lever/Ashby/Workable/Recruitee/SmartRecruiters/Personio board found")
        print()
    print(f"{found}/{len(results)} matched to a posting, {boards}/{len(results)} boards found.")
    elig = [r for r in results if r.get("eligibility", {}).get("verdict", "").startswith("likely eligible")]
    if elig:
        print("Likely remote-eligible from Greece: " + ", ".join(r["company"] for r in elig))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"Full results written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
