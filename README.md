# job-scout

A small personal **MCP server** that aggregates remote + Greek job boards and
surfaces only the roles that match your profile: **Engineering Manager /
leadership** and **AI / forward-deployed / solutions** roles that are
**open to candidates in Greece**.

It does the thing a plain web search can't do well: hit structured job-board
APIs directly, apply a hard Greece-eligibility gate, dedupe, and (optionally)
show you only what's *new* since last time.

## What it does

- Pulls from **Remotive, RemoteOK, Arbeitnow, We Work Remotely** (free public
  APIs/feeds), plus any **Greenhouse** or **Lever** company boards you add to a
  watchlist.
- Classifies each posting into two buckets by title: **EM** and **AI/FDE**.
- Applies a **Greece gate** on the location field:
  - `OK eligible` - says Worldwide / Europe / EMEA / Greece / Athens / ...
  - `CHECK location` - ambiguous (e.g. bare "Remote") - worth a look
  - excluded - clearly locked to another region (US-only, a specific non-GR
    country, APAC, etc.)
- Tags stack hints (`rails`, `dotnet`, `go`, `ai/mcp`) so your strong-fit roles
  float to the top.
- Remembers what it has already shown you (`new_jobs`) so a recurring check
  only reports fresh postings.

## Install

Requires Python 3.10+. Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
cd ~/Projects/job-scout
uv venv
uv pip install -e .
```

Or with plain pip:

```bash
cd ~/Projects/job-scout
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Run / wire into an MCP client

It speaks MCP over stdio. Quick sanity check that it starts:

```bash
uv run job-scout        # or: python -m job_scout.server
```

Add it to your MCP client config (e.g. Claude Desktop
`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "job-scout": {
      "command": "uv",
      "args": ["--directory", "/Users/marky/Projects/job-scout", "run", "job-scout"]
    }
  }
}
```

(If you used a venv instead of uv, point `command` at that venv's Python with
`args: ["-m", "job_scout.server"]` and set a `cwd` of the project dir.)

## Tools

- `search_jobs(bucket="all", include_unverified_location=True, max_results=40)`
  - everything matching, right now. `bucket` = `all` | `em` | `ai`.
- `new_jobs(...)` - same filters, but only postings you haven't been shown
  before (then marks them seen). Use this for a daily/weekly digest.
- `reset_seen()` - clears the seen history.

## Tuning (`config.json`)

- `buckets` - title keywords that define the EM and AI buckets.
- `location_eligible_tokens` / `location_excluded_tokens` - the Greece gate.
  Add/remove tokens to taste (e.g. drop "italy" if you'd consider relocation).
- `stack_hints` - labels applied to matching roles.
- `greenhouse_companies` / `lever_companies` - **watchlist**. Add the board
  token and the server will pull that company's live postings directly:
  - Greenhouse: the slug in `boards.greenhouse.io/<token>` -> add `<token>`.
  - Lever: the slug in `jobs.lever.co/<company>` -> add `<company>`.
- `sources` - toggle any aggregator on/off.

## Known gaps / v2 ideas

- **Greek on-site boards** (kariera.gr, skywalker.gr, jobfind.gr) have no clean
  public API, so Athens-only roles like the ones on those sites aren't covered
  yet. A scraper module is the natural next addition.
- No salary-floor filtering yet (listed salaries are too inconsistent to trust);
  `salary_floor_eur` is reserved in config for when that's worth doing.
- Scheduling is left to the client (run `new_jobs` on a cron / scheduled task).
