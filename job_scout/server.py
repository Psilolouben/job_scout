"""job-scout - an MCP server that aggregates remote + Greek job boards,
filters for Greece-eligible Engineering Manager and AI / forward-deployed
roles, and reports only the matches.

Tools exposed:
    search_jobs - everything matching, right now
    new_jobs    - only postings not seen on a previous run (digest mode)
    reset_seen  - clear the "already seen" memory

Transport:
    Runs over stdio by default (local MCP clients). Set MCP_TRANSPORT=sse
    (or streamable-http) to run as an HTTP service - e.g. when hosted on
    Render - binding to $HOST:$PORT (Render provides PORT automatically).
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from . import sources, store
from .filters import classify_bucket, load_config, location_status, stack_tags

mcp = FastMCP(
    "job-scout",
    host=os.environ.get("HOST", "0.0.0.0"),
    port=int(os.environ.get("PORT", "8000")),
)

BUCKET_LABELS = {
    "em": "Engineering Manager / Leadership",
    "ai": "AI / Forward-Deployed / Solutions",
}


async def _collect(bucket: str, include_verify: bool) -> list[dict]:
    cfg = load_config()
    raw = await sources.gather_all(cfg)
    deduped: dict[str, dict] = {}
    for j in raw:
        b = classify_bucket(j["title"], cfg)
        if b is None:
            continue
        if bucket not in ("all", b):
            continue
        status = location_status(j["location"], cfg)
        if status == "excluded":
            continue
        if status == "verify" and not include_verify:
            continue
        jid = store.job_id(j)
        if jid in deduped:
            continue
        j["_id"] = jid
        j["_bucket"] = b
        j["_loc_status"] = status
        j["_stack"] = stack_tags(j["title"] + " " + j.get("text", ""), cfg)
        deduped[jid] = j

    results = list(deduped.values())
    # Rank: eligible before verify, stack-relevant before not.
    results.sort(key=lambda x: (x["_loc_status"] != "eligible", not x["_stack"]))
    return results


def _format(jobs: list[dict], max_results: int) -> str:
    if not jobs:
        return "No matching roles found this run."
    lines: list[str] = []
    for b in ("em", "ai"):
        group = [j for j in jobs if j["_bucket"] == b][:max_results]
        if not group:
            continue
        lines.append(f"\n## {BUCKET_LABELS[b]} ({len(group)})\n")
        for j in group:
            flag = "OK eligible" if j["_loc_status"] == "eligible" else "CHECK location"
            stack = f" - stack: {', '.join(j['_stack'])}" if j["_stack"] else ""
            sal = f" - {j['salary']}" if j.get("salary") else ""
            loc = j["location"] or "-"
            lines.append(
                f"- **{j['title']}** - {j['company']} - {loc} [{flag}]{sal}{stack}\n  {j['url']}"
            )
    return "\n".join(lines).strip()


@mcp.tool()
async def search_jobs(
    bucket: str = "all",
    include_unverified_location: bool = True,
    max_results: int = 40,
) -> str:
    """Search all configured job sources for Greece-eligible EM and AI/FDE roles.

    Args:
        bucket: 'all', 'em' (engineering management / leadership), or
            'ai' (AI / forward-deployed / solutions).
        include_unverified_location: include roles whose location can't be
            confirmed Greece-eligible (flagged "CHECK location").
        max_results: maximum roles per bucket.
    """
    jobs = await _collect(bucket, include_unverified_location)
    return _format(jobs, max_results)


@mcp.tool()
async def new_jobs(
    bucket: str = "all",
    include_unverified_location: bool = True,
    max_results: int = 40,
) -> str:
    """Like search_jobs, but returns only postings not seen on a previous run,
    then records them as seen. Use this for a recurring digest so you never
    re-read the same posting twice.
    """
    jobs = await _collect(bucket, include_unverified_location)
    seen = store.load_seen()
    fresh = [j for j in jobs if j["_id"] not in seen]
    store.save_seen(seen | {j["_id"] for j in jobs})
    if not fresh:
        return "No new roles since the last check."
    return _format(fresh, max_results)


@mcp.tool()
def reset_seen() -> str:
    """Clear the 'already seen' memory so the next new_jobs call treats
    every posting as new again."""
    store.save_seen(set())
    return "Seen history cleared."


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "stdio":
        mcp.run()
    else:
        # "sse" or "streamable-http" for remote/hosted use (e.g. Render).
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
