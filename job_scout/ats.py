#!/usr/bin/env python3
"""Resolve a (company, title) seen elsewhere - e.g. a LinkedIn job alert -
to the real posting on the company's own applicant-tracking system (ATS).

For each {company, title} in the input file it:
  1. builds a few candidate board slugs from the company name
     (plus manual overrides in OVERRIDES),
  2. probes the public job-board APIs of Greenhouse, Lever (US + EU),
     Ashby, Workable, Recruitee, SmartRecruiters and Personio,
  3. fuzzy-matches the LinkedIn title against the board's postings,
  4. pulls the matched posting's full description and runs a first-pass
     remote-eligibility check for a Greece-based EU citizen, quoting the
     sentences that drove the verdict,
  5. prints the best match with its real location, verdict and apply URL.

Standard library only (blocking urllib); the MCP server calls it via
asyncio.to_thread. poc/ats_lookup.py is a CLI wrapper around it.
"""
from __future__ import annotations

import html
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

UA = {"User-Agent": "job-scout-poc/0.1 (personal job search)", "Accept": "application/json"}
TIMEOUT = 15

# company name (lowercase) -> list of (ats, slug) to try first.
# Add entries here when the automatic slug guess misses.
OVERRIDES: dict[str, list[tuple[str, str]]] = {
    # "marks and spencer": [("workday", "...")],
}

DROP_WORDS = {
    "the", "ltd", "limited", "inc", "gmbh", "ag", "bv", "b.v.", "sa", "s.a.",
    "plc", "llc", "group", "technologies", "technology", "solutions", "invest",
    "holding", "holdings", "company", "co", "labs", "io",
}


# ---------------------------------------------------------------- helpers
def _get(url: str):
    """GET url -> (status, body_text). Network errors return (0, '')."""
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def strip_html(s: str) -> str:
    """HTML (possibly entity-escaped, as Greenhouse returns it) -> plain text."""
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|li|div|h[1-6])>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def slug_candidates(company: str) -> list[str]:
    name = _ascii(company).lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", name)
    core = [w for w in words if w not in DROP_WORDS] or words
    cands = [
        "".join(core),
        "-".join(core),
        "".join(words),
        "-".join(words),
        core[0] if core else "",
    ]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _norm_title(t: str) -> str:
    t = _ascii(t).lower()
    t = re.sub(r"\(m/w/d\)|\(m/f/d\)|\(f/m/d\)|\(m/k\)", " ", t)
    return " ".join(re.findall(r"[a-z0-9]+", t))


GENERIC = {
    "manager", "management", "senior", "sr", "lead", "leader", "head", "staff",
    "principal", "junior", "of", "and", "the", "for", "to", "in", "team", "m",
    "w", "d", "f", "x", "remote", "hybrid", "i", "ii", "iii",
}


def title_score(a: str, b: str) -> float:
    """0..1 similarity. The specific words of the LinkedIn title (not just
    'manager'/'lead') must mostly appear, otherwise the score is capped -
    this stops 'Engineering Manager' matching 'Supplier Manager'."""
    na, nb = _norm_title(a), _norm_title(b)
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb) / max(1, len(ta))
    score = 0.5 * ratio + 0.5 * overlap
    spec = ta - GENERIC
    if spec:
        spec_overlap = len(spec & tb) / len(spec)
        if spec_overlap < 0.5:
            score = min(score, 0.4)
    return round(score, 3)


# ------------------------------------------------------- ATS fetchers
# Each returns a list of postings {title, location, remote, url, posted}
# or None when the board does not exist / is empty.
def greenhouse(slug):
    st, body = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    if st != 200:
        return None
    jobs = json.loads(body).get("jobs", [])
    return [
        {
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "remote": None,
            "url": j.get("absolute_url", ""),
            "posted": j.get("updated_at", ""),
            "description": strip_html(j.get("content", "")),
        }
        for j in jobs
    ] or None


def _lever(slug, host):
    st, body = _get(f"https://{host}/v0/postings/{slug}?mode=json")
    if st != 200:
        return None
    data = json.loads(body)
    if not isinstance(data, list):
        return None
    return [
        {
            "title": j.get("text", ""),
            "location": " / ".join(
                filter(None, [(j.get("categories") or {}).get("location", "")]
                       + (j.get("categories") or {}).get("allLocations", []))
            ),
            "remote": (j.get("workplaceType") == "remote") if j.get("workplaceType") else None,
            "url": j.get("hostedUrl", ""),
            "posted": j.get("createdAt", ""),
            "description": "\n".join(
                [j.get("descriptionPlain", "")]
                + [l.get("text", "") + "\n" + strip_html(l.get("content", ""))
                   for l in j.get("lists", []) or []]
                + [j.get("additionalPlain", "")]
            ).strip(),
        }
        for j in data
    ] or None


def lever(slug):
    return _lever(slug, "api.lever.co")


def lever_eu(slug):
    return _lever(slug, "api.eu.lever.co")


def ashby(slug):
    st, body = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    if st != 200:
        return None
    jobs = json.loads(body).get("jobs", [])
    out = []
    for j in jobs:
        locs = [j.get("location", "")] + [
            s.get("location", "") for s in j.get("secondaryLocations", []) or []
        ]
        out.append(
            {
                "title": j.get("title", ""),
                "location": " / ".join(filter(None, locs)),
                "remote": j.get("isRemote"),
                "url": j.get("jobUrl", ""),
                "posted": j.get("publishedAt", ""),
                "description": j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")),
            }
        )
    return out or None


def workable(slug):
    st, body = _get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
    if st != 200:
        return None
    jobs = json.loads(body).get("jobs", [])
    return [
        {
            "title": j.get("title", ""),
            "location": ", ".join(filter(None, [j.get("city", ""), j.get("country", "")])),
            "remote": j.get("telecommuting"),
            "url": j.get("url") or j.get("shortlink", ""),
            "posted": j.get("published_on", ""),
            "description": strip_html(j.get("description", "")),
        }
        for j in jobs
    ] or None


def recruitee(slug):
    st, body = _get(f"https://{slug}.recruitee.com/api/offers/")
    if st != 200:
        return None
    try:
        offers = json.loads(body).get("offers", [])
    except json.JSONDecodeError:
        return None
    return [
        {
            "title": o.get("title", ""),
            "location": o.get("location", "") or o.get("country", ""),
            "remote": o.get("remote"),
            "url": o.get("careers_url", ""),
            "posted": o.get("published_at") or o.get("created_at", ""),
            "description": strip_html((o.get("description") or "") + "\n" + (o.get("requirements") or "")),
        }
        for o in offers
    ] or None


def smartrecruiters(slug):
    st, body = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    if st != 200:
        return None
    content = json.loads(body).get("content", [])
    out = []
    for p in content:
        loc = p.get("location") or {}
        out.append(
            {
                "title": p.get("name", ""),
                "location": ", ".join(filter(None, [loc.get("city", ""), loc.get("country", "")])),
                "remote": loc.get("remote"),
                "url": f"https://jobs.smartrecruiters.com/{slug}/{p.get('id', '')}",
                "posted": p.get("releasedDate", ""),
                "description": "",
                "_detail": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{p.get('id', '')}",
            }
        )
    return out or None


def personio(slug):
    out = []
    for tld in ("de", "com"):
        st, body = _get(f"https://{slug}.jobs.personio.{tld}/xml")
        if st != 200 or "<workzag-jobs" not in body:
            continue
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            continue
        for p in root.iter("position"):
            pid = p.findtext("id", "")
            out.append(
                {
                    "title": p.findtext("name", ""),
                    "location": p.findtext("office", ""),
                    "remote": None,
                    "url": f"https://{slug}.jobs.personio.{tld}/job/{pid}",
                    "posted": p.findtext("createdAt", ""),
                    "description": strip_html("\n".join(
                        (d.findtext("name", "") + "\n" + d.findtext("value", ""))
                        for d in p.iter("jobDescription")
                    )),
                }
            )
        if out:
            return out
    return None


ATS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "lever-eu": lever_eu,
    "ashby": ashby,
    "workable": workable,
    "recruitee": recruitee,
    "smartrecruiters": smartrecruiters,
    "personio": personio,
}


# -------------------------------------------------- remote eligibility
# First-pass check for: lives in Greece, EU citizen (EU right to work),
# wants fully remote. It quotes evidence; a human/Claude makes the call.
POSITIVE = [
    r"\bgreece\b", r"\bathens\b", r"\banywhere\b", r"\bworldwide\b",
    r"\bwork from anywhere\b", r"\bfully remote\b", r"\bremote[- ]first\b",
    r"remote[^.\n]{0,40}\b(europe|eu|emea|european union)\b",
    r"\b(europe|eu|emea|european union)\b[^.\n]{0,40}\bremote\b",
    r"\b(any|all) (eu|european) countr", r"\bwithin (the )?(eu|europe|emea)\b",
    r"\bacross (the )?(eu|europe|emea)\b", r"\b(cet|cest|eet|utc)\s?[+±]\s?\d",
    r"\bemployer of record\b|\bdeel\b|\bremote\.com\b|\boyster\b",
    r"within \d+ ?(hours?|h)\b[^.\n]{0,30}\b(cet|cest|eet|central european|eastern european)",
    r"\b(central|eastern) european time ?zone",
    r"\bb2b\b|\bcontractor\b",
]
NEGATIVE = [
    r"\bhybrid\b", r"\bon[- ]?site\b", r"\bin[- ]office\b",
    r"\b\d\s*(days?|x)\s*(a|per|/)\s*week\b[^.\n]{0,30}\boffice\b",
    r"\boffice\b[^.\n]{0,40}\b\d\s*days?\b",
    r"\bmust (be|live|reside)[^.\n]{0,40}\b(based|located|resident|in)\b",
    r"\b(only|exclusively)[^.\n]{0,40}\b(based|located|residents?|candidates)\b",
    r"\bright to work in the (uk|united kingdom|us|united states)\b",
    r"\bremote[^.\n]{0,20}\b(within|in) (the )?(uk|united kingdom|us|usa|united states|"
    r"spain|germany|poland|portugal|netherlands|france|ireland|italy|canada)\b",
    r"\brelocat", r"\bvisa sponsorship\b",
]
TZ = re.compile(r"time ?zone|\b(cet|cest|eet|utc)\b|central european|eastern european", re.I)
KEY = re.compile(r"remote|hybrid|on[- ]?site|office|locat|based in|reside|right to work|"
                 r"relocat|time ?zone|\bcet\b|\beet\b|europe|\bemea\b|\beu\b|greece|"
                 r"anywhere|worldwide|countr|b2b|contractor", re.I)


def assess_eligibility(posting: dict) -> dict:
    loc = posting.get("location", "") or ""
    text = (posting.get("description", "") or "")
    blob = f"Location: {loc}\n{text}"
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n", blob) if x.strip()]
    evidence = [x[:300] for x in sentences if KEY.search(x)]

    low = blob.lower()
    pos = [p for p in POSITIVE if re.search(p, low)]
    # A time-zone requirement ("only apply if within 3h of CET") is not a
    # country restriction - Athens is CET+1 - so drop those sentences
    # before looking for restrictions.
    no_tz = "\n".join(x for x in re.split(r"(?<=[.!?])\s+|\n", low)
                      if not TZ.search(x))
    neg = [p for p in NEGATIVE if re.search(p, no_tz)]
    greece = bool(re.search(r"\bgreece\b|\bathens\b", low))

    if greece:
        verdict = "likely eligible (Greece named)"
    elif not text:
        verdict = "unclear (no description fetched)"
    elif pos and not neg:
        verdict = "likely eligible"
    elif neg and not pos:
        verdict = "likely NOT eligible"
    elif pos and neg:
        verdict = "unclear (mixed signals)"
    elif posting.get("remote") is True:
        verdict = "unclear (marked remote, region not stated)"
    else:
        verdict = "unclear (no location signals)"
    return {"verdict": verdict, "positive_hits": pos, "negative_hits": neg,
            "evidence": evidence[:8]}


def _fill_detail(posting: dict) -> None:
    """SmartRecruiters only lists summaries; fetch the matched posting's ad."""
    url = posting.pop("_detail", None)
    if not url or posting.get("description"):
        return
    st, body = _get(url)
    if st != 200:
        return
    secs = (json.loads(body).get("jobAd") or {}).get("sections") or {}
    posting["description"] = strip_html("\n".join(
        (v or {}).get("text", "") for v in secs.values()))


# ------------------------------------------------------------ resolve
def find_board(company: str):
    """Return (ats, slug, postings) for the first board that answers."""
    tries = list(OVERRIDES.get(company.lower(), []))
    for slug in slug_candidates(company):
        for ats in ATS:
            tries.append((ats, slug))
    for ats, slug in tries:
        fn = ATS.get(ats)
        if not fn:
            continue
        postings = fn(slug)
        if postings:
            return ats, slug, postings
    return None, None, []


def resolve(entry: dict, min_score: float) -> dict:
    ats, slug, postings = find_board(entry["company"])
    res = dict(entry, ats=ats, slug=slug, board_size=len(postings), match=None, score=0.0)
    if not postings:
        res["status"] = "no board found"
        return res
    scored = sorted(((title_score(entry["title"], p["title"]), i, p)
                     for i, p in enumerate(postings)), key=lambda x: (-x[0], x[1]))
    sc, _, best = scored[0]
    twins = [p for s_, _, p in scored if s_ >= sc - 0.02 and s_ >= min_score]
    if len(twins) > 1:
        want = [w for w in re.findall(r"[a-z]+", (entry.get("location") or "").lower()) if len(w) > 2]
        def loc_rank(p):
            l = (p.get("location") or "").lower()
            return (0 if "greece" in l or "athens" in l else
                    1 if any(w in l for w in want) else 2)
        best = min(twins, key=loc_rank)
        res["same_title_locations"] = sorted({p.get("location", "") for p in twins})
    res["score"] = sc
    if sc >= min_score:
        _fill_detail(best)
        res["match"] = best
        res["status"] = "matched"
        el_input = dict(best)
        if res.get("same_title_locations"):
            el_input["location"] = " / ".join(res["same_title_locations"])
        res["eligibility"] = assess_eligibility(el_input)
    else:
        res["status"] = "board found, no title match"
        res["closest"] = {k: v for k, v in best.items() if k not in ("description", "_detail")}
    return res
