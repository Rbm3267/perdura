"""
perdura_ingest.py — E3: ingestion adapters (enterprise track).

An enterprise platform is a claim factory: PR reviews, ADRs, incident
postmortems, tickets. Adapters turn one structured item from any of those
streams into the exact same strict-JSON delta an LLM worker produces, then
`ingest()` merges it through `merge_delta()` under the same write lock as a
normal worker turn (`perdura._board`). No privileged side door: every
conductor invariant — schema validation, attribution, supersede-never-
delete — holds for machine-ingested claims exactly as it does for Claude or
Gemini.

Attribution convention: `created_by = "adapter:<source>"` (e.g.
"adapter:adr"). That makes each stream its own attributed worker, so two
consequences fall out for free, with no new machinery:
  - the collision detector (`perdura_memoric.collision_candidates`, which
    keys off `created_by`) finds lexically-close, unlinked claims ACROSS
    streams — an ADR's claim and an incident's claim about the same area —
    the cross-stream collision audits E3 calls for;
  - per-domain track records (`perdura_track.py`) score each stream's
    reliability over time, same as any LLM worker.

Each adapter takes a plain dict — already-fetched item data, not a live API
call. Fetching from a real Jira/GitHub/PagerDuty stream is a thin connector
you write once (see ClaudeWorker/GeminiWorker for the same shape: a small
SDK wrapper around the one stable interface), exactly the boundary that
keeps this module dependency-free and offline-testable.
"""

from perdura import Graph, graph_write_lock, merge_delta


def _require(item: dict, key: str, adapter: str) -> str:
    """A missing required key is a malformed item, not a bug in the
    conductor -- fail with a message that names the adapter and the key
    instead of a bare KeyError."""
    try:
        return item[key]
    except KeyError:
        raise ValueError(
            f"{adapter}_delta: item is missing required key {key!r}") from None


def _attach(question_id, domain_tags, title_if_new):
    """No question_id -> open a new question (ref "q"); edges target that
    ref. A question_id -> edges target the existing node directly; nothing
    new is created for it (resolve_questions can only ever mark an EXISTING
    question, never a ref created in this same delta — merge_delta resolves
    resolve_questions against real node ids, not the ref map)."""
    if question_id is not None:
        return [], question_id, True
    return ([{"ref": "q", "type": "question", "text": title_if_new,
              "confidence": 1.0, "domain_tags": domain_tags}],
            "q", False)


def adr_delta(adr: dict, question_id: str = None) -> dict:
    """Architecture Decision Record -> delta.

    Expected keys: title, decision (str); status ("accepted" | "proposed" |
    "rejected", optional); context, consequences (list[str], optional);
    domain_tags (list[str], optional); resolved (bool, optional — defaults
    to true iff status == "accepted").
    """
    domain_tags = adr.get("domain_tags", [])
    title = _require(adr, "title", "adr")
    nodes, dst, existing = _attach(
        question_id, domain_tags, f"What should we decide: {title}?")
    edges = []

    decision_type = "rejected" if adr.get("status") == "rejected" else "decision"
    nodes.append({"ref": "d", "type": decision_type,
                  "text": _require(adr, "decision", "adr"),
                  "confidence": 1.0 if adr.get("status") == "accepted" else 0.5,
                  "domain_tags": domain_tags})
    edges.append({"type": "answers", "src": "d", "dst": dst})

    for i, ctx in enumerate(adr.get("context", [])):
        ref = f"ctx{i}"
        nodes.append({"ref": ref, "type": "claim", "text": ctx,
                      "confidence": 0.6, "domain_tags": domain_tags})
        edges.append({"type": "depends_on", "src": "d", "dst": ref})

    for i, cons in enumerate(adr.get("consequences", [])):
        ref = f"ev{i}"
        nodes.append({"ref": ref, "type": "evidence", "text": cons,
                      "confidence": 0.7, "domain_tags": domain_tags})
        edges.append({"type": "supports", "src": ref, "dst": "d"})

    resolved = adr.get("resolved", adr.get("status") == "accepted")
    return {"new_nodes": nodes, "new_edges": edges, "supersedes": [],
            "resolve_questions": [dst] if existing and resolved else []}


def incident_delta(incident: dict, question_id: str = None) -> dict:
    """Incident postmortem -> delta.

    Expected keys: title, root_cause (str); contributing_factors,
    action_items (list[str], optional); domain_tags (list[str], optional —
    typically the affected services); resolved (bool, optional — defaults
    to true: a postmortem is written after the cause is established).
    """
    domain_tags = incident.get("domain_tags", [])
    title = _require(incident, "title", "incident")
    nodes, dst, existing = _attach(
        question_id, domain_tags, f"What caused: {title}?")
    edges = []

    nodes.append({"ref": "rc", "type": "claim",
                  "text": _require(incident, "root_cause", "incident"),
                  "confidence": 0.8, "domain_tags": domain_tags})
    edges.append({"type": "answers", "src": "rc", "dst": dst})

    for i, factor in enumerate(incident.get("contributing_factors", [])):
        ref = f"cf{i}"
        nodes.append({"ref": ref, "type": "evidence", "text": factor,
                      "confidence": 0.6, "domain_tags": domain_tags})
        edges.append({"type": "supports", "src": ref, "dst": "rc"})

    for i, item in enumerate(incident.get("action_items", [])):
        ref = f"ai{i}"
        nodes.append({"ref": ref, "type": "claim", "text": item,
                      "confidence": 0.7, "domain_tags": domain_tags})
        edges.append({"type": "depends_on", "src": ref, "dst": "rc"})

    resolved = incident.get("resolved", True)
    return {"new_nodes": nodes, "new_edges": edges, "supersedes": [],
            "resolve_questions": [dst] if existing and resolved else []}


def ticket_delta(ticket: dict, question_id: str = None) -> dict:
    """Support/work ticket -> delta.

    Expected keys: title, description (str); resolution (str, optional);
    labels (list[str], optional -> domain_tags unless domain_tags given
    explicitly); status, resolved (optional — defaults to true iff status
    is closed/resolved/done).
    """
    domain_tags = ticket.get("domain_tags") or ticket.get("labels", [])
    title = _require(ticket, "title", "ticket")
    nodes, dst, existing = _attach(question_id, domain_tags, title)
    edges = []

    nodes.append({"ref": "c", "type": "claim",
                  "text": _require(ticket, "description", "ticket"),
                  "confidence": 0.5, "domain_tags": domain_tags})
    edges.append({"type": "answers", "src": "c", "dst": dst})

    if ticket.get("resolution"):
        nodes.append({"ref": "r", "type": "evidence",
                      "text": ticket["resolution"],
                      "confidence": 0.6, "domain_tags": domain_tags})
        edges.append({"type": "supports", "src": "r", "dst": "c"})

    resolved = ticket.get(
        "resolved", ticket.get("status") in ("closed", "resolved", "done"))
    return {"new_nodes": nodes, "new_edges": edges, "supersedes": [],
            "resolve_questions": [dst] if existing and resolved else []}


def pr_review_delta(pr: dict, question_id: str = None) -> dict:
    """A pull request plus its review comments -> delta.

    Expected keys: title (str); body (str, optional); comments
    (list[{"body": str}], optional); domain_tags (list[str], optional);
    merged, resolved (bool, optional — resolved defaults to `merged`).
    """
    domain_tags = pr.get("domain_tags", [])
    title = _require(pr, "title", "pr")
    nodes, dst, existing = _attach(
        question_id, domain_tags, f"Should we merge: {title}?")
    edges = []

    nodes.append({"ref": "c", "type": "claim", "text": pr.get("body") or title,
                  "confidence": 0.5, "domain_tags": domain_tags})
    edges.append({"type": "answers", "src": "c", "dst": dst})

    for i, comment in enumerate(pr.get("comments", [])):
        ref = f"cm{i}"
        nodes.append({"ref": ref, "type": "evidence", "text": comment["body"],
                      "confidence": 0.5, "domain_tags": domain_tags})
        edges.append({"type": "supports", "src": ref, "dst": "c"})

    resolved = pr.get("resolved", pr.get("merged", False))
    return {"new_nodes": nodes, "new_edges": edges, "supersedes": [],
            "resolve_questions": [dst] if existing and resolved else []}


ADAPTERS = {
    "adr": adr_delta,
    "incident": incident_delta,
    "ticket": ticket_delta,
    "pr": pr_review_delta,
}


def ingest(graph_path: str, source: str, item: dict, question_id: str = None):
    """Build a delta with the named adapter and merge it through the normal
    conductor path: reload, merge under the write lock, save — the exact
    sequence `_board` runs for an LLM worker turn. Returns (accepted,
    rejected)."""
    delta = ADAPTERS[source](item, question_id=question_id)
    worker = f"adapter:{source}"
    with graph_write_lock(graph_path):
        g = Graph(graph_path)
        acc, rej = merge_delta(g, delta, worker)
        g.save()
    return acc, rej
