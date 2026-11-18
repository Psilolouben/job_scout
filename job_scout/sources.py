"""Job source fetchers.

Each fetcher returns a list of normalized dicts:
    {source, title, company, location, url, salary, text}

Every network call is wrapped so a single failing source never breaks a
run - it just contributes nothing. Add more companies to watch via the
``greenhouse_companies`` / ``lever_companies`` lists in config.json.
"""
from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET

import httpx

HEADERS = {"User-Agent": "job-scout/0.1 (personal job search tool)"}
TIMEOUT = httpx.Timeout(20.0)

# Terms used against keyword-search APIs (Remotive). Broad on purpose;
# the bucket classifier in filters.py does the real narrowing.
SEARCH_TERMS = [
    "engineering manager",
    "engineering lead",
    "forward deployed",
    "solutions engineer",
    "solutions architect",
    "ai engineer",
]


def _norm(source, title, company, location, url, salary="", text=""):
    return {
        "source": source,
        "title": (title or "").strip(),
        "company": (company or "").strip(),
        "location": (location or "").strip(),
        "url": (url or "").strip(),
        "salary": salary.strip() if isinstance(salary, str) else str(salary or ""),
        "text": text or "",
    }


async def fetch_remotive(client: httpx.AsyncClient) -> list[dict]:
    out = []
    for term in SEARCH_TERMS:
        try:
            r = await client.get(
                "https://remotive.com/api/remote-jobs",
                params={"search": term, "limit": 50},
            )
            r.raise_for_status()
            for j in r.json().get("jobs", []):
                out.append(
                    _norm(
                        "remotive",
                        j.get("title"),
                        j.get("company_name"),
                        j.get("candidate_required_location"),
                        j.get("url"),
                        j.get("salary"),
                        j.get("description", ""),
                    )
                )
        except Exception:
            continue
    return out


async def fetch_remoteok(client: httpx.AsyncClient) -> list[dict]:
    out = []
    try:
        r = await client.get("https://remoteok.com/api")
        r.raise_for_status()
        for j in r.json():
            if not isinstance(j, dict) or "position" not in j:
                continue  # first element is a legal notice
            out.append(
                _norm(
                    "remoteok",
                    j.get("position"),
                    j.get("company"),
                    j.get("location") or "Remote",
                    j.get("url"),
                    "",
                    " ".join(j.get("tags", []) or []) + " " + (j.get("description", "") or ""),
                )
            )
    except Exception:
        pass
    return out


async def fetch_arbeitnow(client: httpx.AsyncClient) -> list[dict]:
    out = []
    try:
        r = await client.get("https://www.arbeitnow.com/api/job-board-api")
        r.raise_for_status()
        for j in r.json().get("data", []):
            loc = j.get("location") or ("Remote" if j.get("remote") else "")
            out.append(
                _norm(
                    "arbeitnow",
                    j.get("title"),
                    j.get("company_name"),
                    loc,
                    j.get("url"),
                    "",
                    " ".join(j.get("tags", []) or []) + " " + (j.get("description", "") or ""),
                )
            )
    except Exception:
        pass
    return out


async def fetch_weworkremotely(client: httpx.AsyncClient) -> list[dict]:
    out = []
    try:
        r = await client.get(
            "https://weworkremotely.com/categories/remote-programming-jobs.rss"
        )
        r.raise_for_status()
        root = ET.fromstring(r.text)
        for item in root.iter("item"):
            title = item.findtext("title") or ""
            company, _, role = title.partition(":")  # WWR titles are "Company: Role"
            # WWR puts a broad <region> ("Anywhere in the World") alongside a
            # <country> that holds the real restriction (e.g. UK-only). When a
            # country is given it wins; otherwise fall back to the region.
            country = (item.findtext("country") or "").strip()
            region = country or item.findtext("region") or "Remote"
            out.append(
                _norm(
                    "weworkremotely",
                    role.strip() or title,
                    company.strip(),
                    region,
                    item.findtext("link"),
                    "",
                    item.findtext("description", "") or "",
                )
            )
    except Exception:
        pass
    return out


async def fetch_greenhouse(client: httpx.AsyncClient, token: str) -> list[dict]:
    out = []
    try:
        r = await client.get(
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
            params={"content": "false"},
        )
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            out.append(
                _norm(f"greenhouse:{token}", j.get("title"), token, loc, j.get("absolute_url"))
            )
    except Exception:
        pass
    return out


async def fetch_lever(client: httpx.AsyncClient, company: str) -> list[dict]:
    out = []
    try:
        r = await client.get(
            f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"}
        )
        r.raise_for_status()
        for j in r.json():
            loc = (j.get("categories") or {}).get("location", "")
            out.append(
                _norm(f"lever:{company}", j.get("text"), company, loc, j.get("hostedUrl"))
            )
    except Exception:
        pass
    return out


async def gather_all(cfg: dict) -> list[dict]:
    """Run every enabled source concurrently and return the merged list."""
    async with httpx.AsyncClient(
        headers=HEADERS, timeout=TIMEOUT, follow_redirects=True
    ) as client:
        tasks = []
        srcs = cfg.get("sources", {})
        if srcs.get("remotive", True):
            tasks.append(fetch_remotive(client))
        if srcs.get("remoteok", True):
            tasks.append(fetch_remoteok(client))
        if srcs.get("arbeitnow", True):
            tasks.append(fetch_arbeitnow(client))
        if srcs.get("weworkremotely", True):
            tasks.append(fetch_weworkremotely(client))
        for token in cfg.get("greenhouse_companies", []):
            tasks.append(fetch_greenhouse(client, token))
        for company in cfg.get("lever_companies", []):
            tasks.append(fetch_lever(client, company))
        results = await asyncio.gather(*tasks, return_exceptions=True)

    jobs: list[dict] = []
    for res in results:
        if isinstance(res, list):
            jobs.extend(res)
    return jobs
