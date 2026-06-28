"""Integration probe (Task 1): petanque state-handle liveness.

Records the empirical finding that an OLD petanque state handle stays runnable
after a NEWER state is produced from the same parent in the same session — so the
DFS proof search may hold many live handles rather than replaying from root.

Skipped by default: it needs a running pet-server AND a generated
`/work/proofs/targets/Addloop_gen.v`, so it only runs inside the agent container:

    docker compose -f docker/compose.yaml run --rm \
        --entrypoint python -m pytest /app/tests/test_petanque_state.py -q

Run it with CLOQ_RUN_PETANQUE_PROBE=1 set.
"""
from __future__ import annotations

import os

import pytest

# Path resolves on the SERVER (rocq container) side; the gen file exists after a run.
FILE = "/work/proofs/targets/Addloop_gen.v"
THEOREM = "addloop_timing_gen"
# `idtac.` is always valid, so a failure of the late run isolates a STALE HANDLE
# (not a rejected tactic) — exactly the property under test.
LATE_TAC = "idtac."

pytestmark = pytest.mark.skipif(
    os.environ.get("CLOQ_RUN_PETANQUE_PROBE") != "1",
    reason="needs a running pet-server + generated Addloop_gen.v; "
           "set CLOQ_RUN_PETANQUE_PROBE=1 to run inside the agent container",
)


def test_old_handle_stays_live_after_newer_state():
    from cloq_agent.config import load_config
    from cloq_agent.proof.petanque_driver import PetanqueDriver

    cfg = load_config()
    with PetanqueDriver(cfg.petanque) as d:
        s0 = d.start(FILE, THEOREM)
        assert s0.ok, f"could not start proof: {s0.error}"

        s1 = d.run(s0.state, "intros.")
        assert s1.ok, f"intros. failed: {s1.error}"
        s1b = d.run(s0.state, "intros.")  # newer state from the same parent
        assert s1b.ok, f"second intros. failed: {s1b.error}"

        # Crux: run from s1 AFTER s1b was produced. Finding: this still succeeds.
        late = d.run(s1.state, LATE_TAC)
        assert late.ok, (
            "old handle went stale after a newer state was produced "
            f"(must-replay regime): {late.error}"
        )


def test_replay_from_root_reaches_same_goal():
    from cloq_agent.config import load_config
    from cloq_agent.proof.petanque_driver import PetanqueDriver

    cfg = load_config()
    with PetanqueDriver(cfg.petanque) as d:
        replayed = d.replay_from_root(FILE, THEOREM, ["intros."])
        assert replayed.ok, f"replay_from_root failed: {replayed.error}"

        bad = d.replay_from_root(FILE, THEOREM, ["intros.", "this_is_not_a_tactic."])
        assert not bad.ok
        assert "replay failed at" in (bad.error or "")
