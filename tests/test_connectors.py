"""GitHub PR connector (E3 follow-up) — fetch_github_prs/sync_github_prs
take an injectable `fetch`, so this whole module is offline and
deterministic, exactly like perdura_ingest.py's adapters: no live API
calls, no token, no network."""

import json

import pytest

from perdura import Graph
from perdura_connectors import fetch_github_prs, sync_github_prs

PR_1 = {"number": 1, "title": "Add RLS policies", "body": "Adds FORCE RLS.",
        "merged_at": "2026-06-01T00:00:00Z", "labels": [{"name": "storage"}]}
PR_2 = {"number": 2, "title": "Fix flaky test", "body": None,
        "merged_at": None, "labels": []}
COMMENTS_1 = [{"body": "LGTM, verified against a non-superuser role."}]
COMMENTS_2 = []


def _fake_fetch(calls):
    def fetch(url, token=None):
        calls.append((url, token))
        if url.endswith("/pulls?state=closed&per_page=20&sort=created&direction=desc"):
            return [PR_2, PR_1]   # newest first, like the real API
        if url.endswith("/pulls/1/comments"):
            return COMMENTS_1
        if url.endswith("/pulls/2/comments"):
            return COMMENTS_2
        raise AssertionError(f"unexpected URL: {url}")
    return fetch


def test_fetch_github_prs_maps_to_pr_review_delta_shape():
    calls = []
    items = fetch_github_prs("acme/widgets", token="tok",
                             fetch=_fake_fetch(calls))
    numbers = {n for n, _ in items}
    assert numbers == {1, 2}
    item1 = next(item for n, item in items if n == 1)
    assert item1["title"] == "Add RLS policies"
    assert item1["merged"] is True
    assert item1["domain_tags"] == ["storage"]
    assert item1["comments"] == [{"body": "LGTM, verified against a "
                                          "non-superuser role."}]
    item2 = next(item for n, item in items if n == 2)
    assert item2["merged"] is False and item2["body"] == ""
    assert all(token == "tok" for _, token in calls)


def test_fetch_github_prs_skips_already_synced_numbers():
    calls = []
    items = fetch_github_prs("acme/widgets", since_number=1,
                             fetch=_fake_fetch(calls))
    assert {n for n, _ in items} == {2}
    assert not any(url.endswith("/pulls/1/comments") for url, _ in calls)


def test_sync_github_prs_ingests_and_advances_cursor(tmp_path):
    path = str(tmp_path / "g.json")
    result = sync_github_prs(path, "acme/widgets",
                             fetch=_fake_fetch([]))
    assert result["prs_synced"] == 2
    assert result["accepted"] > 0 and result["rejected"] == 0
    assert result["since_number"] == 2

    g = Graph(path)
    claims = [n for n in g.nodes.values() if n.type == "claim"
             and n.created_by == "adapter:pr"]
    assert len(claims) == 2
    evidence = [n for n in g.nodes.values() if n.type == "evidence"]
    assert len(evidence) == 1   # only PR #1 had a review comment


def test_sync_github_prs_is_idempotent_via_cursor(tmp_path):
    path = str(tmp_path / "g.json")
    first = sync_github_prs(path, "acme/widgets", fetch=_fake_fetch([]))
    second = sync_github_prs(path, "acme/widgets", since_number=first["since_number"],
                             fetch=_fake_fetch([]))
    assert second["prs_synced"] == 0
    assert second["accepted"] == 0
