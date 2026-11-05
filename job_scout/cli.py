"""Tiny command-line runner, so you can see results without an MCP client.

Examples:
    python -m job_scout.cli                 # all buckets, including unverified locations
    python -m job_scout.cli --bucket em     # engineering-management roles only
    python -m job_scout.cli --bucket ai     # AI / forward-deployed / solutions only
    python -m job_scout.cli --new           # only postings not seen on a previous run
    python -m job_scout.cli --strict        # drop roles whose location isn't confirmed eligible
"""
from __future__ import annotations

import argparse
import asyncio

from . import store
from .server import _collect, _format


def main() -> None:
    p = argparse.ArgumentParser(
        description="Scan job boards for Greece-eligible Engineering Manager / AI roles."
    )
    p.add_argument("--bucket", choices=["all", "em", "ai"], default="all")
    p.add_argument(
        "--new",
        action="store_true",
        help="only roles not seen on a previous run (then mark them seen)",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="exclude roles whose location can't be confirmed Greece-eligible",
    )
    p.add_argument("--max", type=int, default=40, help="max results per bucket")
    args = p.parse_args()

    jobs = asyncio.run(_collect(args.bucket, include_verify=not args.strict))

    if args.new:
        seen = store.load_seen()
        fresh = [j for j in jobs if j["_id"] not in seen]
        store.save_seen(seen | {j["_id"] for j in jobs})
        jobs = fresh

    print(_format(jobs, args.max))


if __name__ == "__main__":
    main()
