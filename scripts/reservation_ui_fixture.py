"""Create an isolated synthetic paused reservation snapshot for native UI acceptance."""

from pathlib import Path
from uuid import uuid4

from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore


def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    store = RunStore(root / "fixture.sqlite3")
    run = store.create_run(
        objective="Inspect interrupted token reservation",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model="synthetic-fixture",
        max_total_tokens=1000,
        max_output_tokens=256,
    )
    token = store.acquire_lease(run["id"], "synthetic-fixture")
    assert token is not None
    store.transition(run["id"], RunState.PLANNING, owner=token)
    inference = store.reserve_parent_inference(run["id"], token, limit=3)
    store.reserve_tokens(run["id"], token, inference, 44, 256)
    store.transition(
        run["id"], RunState.PAUSED, reason="synthetic_interrupted_request", owner=token
    )
    store.release_lease(run["id"], token)
    print(store.path)


if __name__ == "__main__":
    main()
