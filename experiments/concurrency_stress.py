"""
experiments/concurrency_stress.py — acceptance test for issue #10.

Two writer processes hammer one graph file concurrently through the same
reload-under-lock merge path the conductor and MCP station use. Zero
accepted deltas may be lost: final claim count must equal writers x deltas.

    python experiments/concurrency_stress.py
"""

import multiprocessing
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

GRAPH = "/tmp/stress_graph.json"
WRITERS, DELTAS = 2, 25


def writer(name: str):
    from perdura import Graph, graph_write_lock, merge_delta
    for i in range(DELTAS):
        delta = {"new_nodes": [{
            "ref": "a", "type": "claim",
            "text": f"{name} distinct claim number {i} about topic "
                    f"{name}-{i} with unique payload {os.urandom(4).hex()}",
            "confidence": 0.6, "domain_tags": []}],
            "new_edges": []}
        with graph_write_lock(GRAPH):
            g = Graph(GRAPH)
            merge_delta(g, delta, name)
            g.save()


if __name__ == "__main__":
    from perdura import Graph
    for f in (GRAPH, GRAPH + ".lock"):
        Path(f).unlink(missing_ok=True)
    Graph(GRAPH).save()

    procs = [multiprocessing.Process(target=writer, args=(f"w{n}",))
             for n in range(WRITERS)]
    [p.start() for p in procs]
    [p.join() for p in procs]
    assert all(p.exitcode == 0 for p in procs), "writer crashed"

    g = Graph(GRAPH)
    claims = [n for n in g.nodes.values() if n.type == "claim"]
    expected = WRITERS * DELTAS
    assert len(claims) == expected, f"LOST UPDATES: {len(claims)}/{expected}"
    assert len(g.log) == expected, f"log entries lost: {len(g.log)}/{expected}"
    print(f"stress OK: {len(claims)}/{expected} claims survived "
          f"{WRITERS} concurrent writers, log intact")
