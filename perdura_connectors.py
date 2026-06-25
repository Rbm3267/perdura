"""
perdura_connectors.py — E3 follow-up: a real connector, not just an adapter.

perdura_ingest.py's adapters take plain, already-fetched dicts by design —
"the boundary that keeps this module dependency-free and offline-testable"
(perdura_ingest.py module docstring). Nobody runs an enterprise pilot by
hand-typing JSON, though, so this module is the other half: a thin client
that actually talks to a stream (GitHub PRs, to start) and maps what it
gets back into the shape `pr_review_delta` expects, then ingests it through
the normal conductor path. Same offline-testability discipline as the rest
of the codebase: every network call goes through one injectable `fetch`
function, so tests drive the mapping and sync logic with a fake transport
and never touch the network (see tests/test_connectors.py).

Only a cursor (the highest PR number synced so far) is needed between runs
to avoid re-ingesting the same PR; this module is pure/stateless about it —
the CLI wrapper (`perdura.py sync-github`) is the one that reads/writes the
cursor file, the same way a customer's own cron job would.

Stdlib only (urllib), matching perdura_sso.py's JWKS fetch — no new
dependency for one HTTP client.

    python perdura.py sync-github --graph g.json --repo owner/name \\
        --token "$GITHUB_TOKEN"

Jira, PagerDuty, and Slack connectors are the same shape: fetch -> map to
the adapter's expected dict -> `ingest()`. This module is the first one;
the pattern, not the GitHub specifics, is the reusable part.
"""

import json
import urllib.error
import urllib.request

from perdura_ingest import ingest

GITHUB_API = "https://api.github.com"


def _default_fetch(url: str, token: str = None) -> object:
    """GET `url`, return the parsed JSON body. Raises urllib.error.HTTPError
    on a non-2xx response (e.g. a bad/expired token, or rate-limiting)."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "perdura-connector",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _pr_to_item(pr: dict, comments: list, domain_tags: list = None) -> dict:
    """Map one GitHub PR (+ its review comments) to the dict
    `pr_review_delta` expects."""
    return {
        "title": pr["title"],
        "body": pr.get("body") or "",
        "comments": [{"body": c["body"]} for c in comments],
        "merged": bool(pr.get("merged_at")),
        "domain_tags": domain_tags
                       if domain_tags is not None
                       else [lbl["name"] for lbl in pr.get("labels", [])],
    }


def fetch_github_prs(repo: str, token: str = None, state: str = "closed",
                     since_number: int = 0, per_page: int = 20,
                     fetch=None) -> list:
    """All of `repo`'s PRs (newest first, GitHub's default sort) with
    `number > since_number`, each paired with its review comments.

    Pages until a PR at or below `since_number` is reached, or a page
    comes back shorter than `per_page` (no more pages) -- stopping after
    page one would permanently skip older un-synced PRs whenever more
    than `per_page` PRs land between two sync runs, since the cursor only
    ever advances to the newest number seen.

    Returns a list of (pr_number, item) pairs, item already shaped for
    `pr_review_delta`. Caller (`sync_github_prs`) tracks the new
    high-water mark; this function makes no assumption about persistence.
    """
    fetch = fetch or _default_fetch
    out = []
    page = 1
    while True:
        prs = fetch(f"{GITHUB_API}/repos/{repo}/pulls"
                   f"?state={state}&per_page={per_page}&sort=created&direction=desc"
                   f"&page={page}", token)
        if not prs:
            break
        reached_cursor = False
        for pr in prs:
            if pr["number"] <= since_number:
                reached_cursor = True
                break
            comments = fetch(
                f"{GITHUB_API}/repos/{repo}/pulls/{pr['number']}/comments", token)
            out.append((pr["number"], _pr_to_item(pr, comments)))
        if reached_cursor or len(prs) < per_page:
            break
        page += 1
    return out


def sync_github_prs(graph_path: str, repo: str, token: str = None,
                    state: str = "closed", since_number: int = 0,
                    per_page: int = 20, question_id: str = None,
                    fetch=None) -> dict:
    """Fetch new PRs (number > since_number) from `repo` and ingest each
    as a `pr` delta through the normal conductor path (same write lock,
    validation, attribution as any other ingest source).

    Returns {"prs_synced", "accepted", "rejected", "since_number"} — the
    last is the new cursor high-water mark, for the caller to persist.
    """
    items = fetch_github_prs(repo, token=token, state=state,
                             since_number=since_number, per_page=per_page,
                             fetch=fetch)
    accepted = rejected = 0
    max_number = since_number
    for number, item in items:
        acc, rej = ingest(graph_path, "pr", item, question_id=question_id)
        accepted += acc
        rejected += rej
        max_number = max(max_number, number)
    return {"prs_synced": len(items), "accepted": accepted,
            "rejected": rejected, "since_number": max_number}
