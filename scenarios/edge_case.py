"""Edge-case scenario — cascading disruption.

One of the three runnable scenarios (build spec §10/§11, M5). A cascading
disruption (e.g. the first reroute is itself hit) exercises re-planning under
tightening hard constraints.

STATUS: stub. The happy path is wired (``scenarios/happy_path.py``); the edge
and failure scenarios land with M5 (resilience + scenarios).
"""

# TODO(M5): drive the orchestrator through a cascading-disruption fixture and
# assert the planner re-ranks options against the still-binding hard deadline.

if __name__ == "__main__":
    raise SystemExit("edge_case scenario is a stub (build spec M5).")
