"""E3 ingestion adapters — PR/ADR/incident/ticket streams propose deltas
through the exact same merge path as an LLM worker turn (docs/enterprise.md
§6). Offline and deterministic: adapters take plain dicts, no live API
calls, no model server."""

import pytest

from perdura import Graph, merge_delta
from perdura_ingest import (
    ADAPTERS, adr_delta, incident_delta, ingest, pr_review_delta, ticket_delta,
)
from perdura_memoric import collision_candidates


def test_adr_delta_shape_and_merge(graph):
    delta = adr_delta({
        "title": "Use Postgres for the multi-tenant store",
        "status": "accepted",
        "decision": "We will use Postgres with RLS for tenant isolation.",
        "context": ["Tenants must not see each other's data."],
        "consequences": ["Operators need a non-superuser app role."],
        "domain_tags": ["storage"],
    })
    acc, rej = merge_delta(graph, delta, worker="adapter:adr")
    assert rej == 0 and acc == len(delta["new_nodes"]) + len(delta["new_edges"])

    by_type = {}
    for n in graph.nodes.values():
        by_type.setdefault(n.type, []).append(n)
    assert len(by_type["question"]) == 1
    assert len(by_type["decision"]) == 1
    assert len(by_type["claim"]) == 1       # the context item
    assert len(by_type["evidence"]) == 1    # the consequence
    assert all(n.domain_tags == ["storage"] for n in graph.nodes.values())
    assert all(n.created_by == "adapter:adr" for n in graph.nodes.values())


def test_adr_rejected_status_creates_rejected_node_not_decision(graph):
    delta = adr_delta({
        "title": "Use a hand-rolled crypto scheme",
        "status": "rejected",
        "decision": "We will not hand-roll crypto.",
    })
    acc, rej = merge_delta(graph, delta, worker="adapter:adr")
    assert rej == 0
    types = {n.type for n in graph.nodes.values()}
    assert "rejected" in types and "decision" not in types


def test_adr_attaches_to_existing_question_and_resolves_it(graph):
    q = graph.add_node("question", "How should we isolate tenants?",
                       created_by="user", confidence=1.0)
    delta = adr_delta({
        "title": "Use Postgres RLS",
        "status": "accepted",
        "decision": "Use Postgres RLS, FORCEd.",
    }, question_id=q)
    acc, rej = merge_delta(graph, delta, worker="adapter:adr")
    assert rej == 0
    assert not any(n.type == "question" and n.id != q for n in graph.nodes.values())
    assert graph.nodes[q].status == "resolved"


def test_adr_proposed_status_does_not_resolve_existing_question(graph):
    q = graph.add_node("question", "How should we isolate tenants?",
                       created_by="user", confidence=1.0)
    delta = adr_delta({
        "title": "Maybe Postgres RLS",
        "status": "proposed",
        "decision": "Considering Postgres RLS.",
    }, question_id=q)
    merge_delta(graph, delta, worker="adapter:adr")
    assert graph.nodes[q].status == "open"


def test_incident_delta_shape_and_domain_tags(graph):
    delta = incident_delta({
        "title": "Billing outage",
        "root_cause": "Connection pool exhaustion caused the outage.",
        "contributing_factors": ["No backpressure on the queue."],
        "action_items": ["Add a hard cap on the connection pool."],
        "domain_tags": ["billing"],
    })
    acc, rej = merge_delta(graph, delta, worker="adapter:incident")
    assert rej == 0
    by_type = {}
    for n in graph.nodes.values():
        by_type.setdefault(n.type, []).append(n)
    assert len(by_type["claim"]) == 2        # root_cause + action item
    assert len(by_type["evidence"]) == 1     # contributing factor
    assert all(n.domain_tags == ["billing"] for n in graph.nodes.values())
    # a postmortem is written after the cause is known -> resolves by default
    q = next(n for n in graph.nodes.values() if n.type == "question")
    # no existing question_id was given, so nothing *could* resolve yet
    assert q.status == "open"


def test_incident_resolved_defaults_true_when_attached(graph):
    q = graph.add_node("question", "What caused the billing outage?",
                       created_by="user", confidence=1.0)
    delta = incident_delta({
        "title": "Billing outage",
        "root_cause": "Connection pool exhaustion caused the outage.",
    }, question_id=q)
    merge_delta(graph, delta, worker="adapter:incident")
    assert graph.nodes[q].status == "resolved"


def test_ticket_delta_uses_labels_as_domain_tags_and_resolved_flag(graph):
    q = graph.add_node("question", "Why is mobile login broken?",
                       created_by="user", confidence=1.0)
    open_delta = ticket_delta({
        "title": "Login button broken on mobile",
        "description": "Login CTA does nothing on iOS Safari.",
        "labels": ["mobile", "auth"], "status": "open",
    }, question_id=q)
    merge_delta(graph, open_delta, worker="adapter:ticket")
    assert graph.nodes[q].status == "open"
    claim = next(n for n in graph.nodes.values() if n.type == "claim")
    assert claim.domain_tags == ["mobile", "auth"]

    closed_delta = ticket_delta({
        "title": "Login button broken on mobile (again)",
        "description": "Regression of the same bug.",
        "resolution": "Reverted the offending CSS change.",
        "status": "closed",
    }, question_id=q)
    merge_delta(graph, closed_delta, worker="adapter:ticket")
    assert graph.nodes[q].status == "resolved"
    assert any(n.type == "evidence" for n in graph.nodes.values())


def test_pr_review_delta_comments_become_evidence_and_merged_resolves(graph):
    q = graph.add_node("question", "Should we ship the RLS migration?",
                       created_by="user", confidence=1.0)
    delta = pr_review_delta({
        "title": "Add RLS policies",
        "body": "Adds FORCE ROW LEVEL SECURITY to every tenant table.",
        "comments": [{"body": "LGTM, verified against a non-superuser role."}],
        "merged": True,
    }, question_id=q)
    acc, rej = merge_delta(graph, delta, worker="adapter:pr")
    assert rej == 0
    assert sum(1 for n in graph.nodes.values() if n.type == "evidence") == 1
    assert graph.nodes[q].status == "resolved"


def test_cross_stream_collision_detected(graph):
    """The cross-department failure mode E3 targets: an incident's claimed
    root cause and an ADR's context claim, lexically close, never linked,
    sourced from two different streams -- exactly what --audit-every is
    meant to surface (docs/enterprise.md §1)."""
    incident = incident_delta({
        "title": "Billing outage",
        "root_cause": "Connection pool exhaustion caused the outage in the billing service.",
        "domain_tags": ["billing"],
    })
    adr = adr_delta({
        "title": "Billing service outage RCA",
        "decision": "Add a connection pool hard cap.",
        "context": ["The billing service outage was not caused by connection pool exhaustion."],
        "domain_tags": ["billing"],
    })
    merge_delta(graph, incident, worker="adapter:incident")
    merge_delta(graph, adr, worker="adapter:adr")

    pairs = collision_candidates(graph)
    sources = [{a.created_by, b.created_by} for a, b in pairs]
    assert {"adapter:incident", "adapter:adr"} in sources


def test_ingest_merges_under_lock_and_persists(tmp_path):
    path = str(tmp_path / "g.json")
    acc, rej = ingest(path, "ticket", {
        "title": "Login button broken on mobile",
        "description": "Users report the login CTA does nothing on iOS Safari.",
        "labels": ["mobile", "auth"], "status": "open",
    })
    assert rej == 0 and acc > 0

    reloaded = Graph(path)
    claims = [n for n in reloaded.nodes.values() if n.type == "claim"]
    assert len(claims) == 1
    assert claims[0].created_by == "adapter:ticket"
    assert claims[0].domain_tags == ["mobile", "auth"]


def test_ingest_unknown_adapter_raises(tmp_path):
    with pytest.raises(KeyError):
        ingest(str(tmp_path / "g.json"), "carrier-pigeon", {})


def test_all_adapters_registered():
    assert set(ADAPTERS) == {"adr", "incident", "ticket", "pr"}
