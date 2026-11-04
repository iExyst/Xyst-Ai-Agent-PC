from xaa.storage.memory import MemoryStore
from xaa.storage.runs import RunStore


def test_explicit_preference_edit_delete_and_scope_search(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "memory.sqlite3")
    memories = MemoryStore(runs)
    record = memories.save_preference("user-one", "Use concise Python explanations")
    assert record["provenance"] == "user"
    assert memories.search("user-one", "Python")[0]["id"] == record["id"]
    assert memories.search("user-two", "Python") == []
    memories.save_preference(
        "user-one", "Use detailed TypeScript explanations", memory_id=record["id"]
    )
    assert memories.search("user-one", "Python") == []
    assert MemoryStore(RunStore(runs.path)).search("user-one", "TypeScript")
    assert not memories.delete("user-two", record["id"])
    assert memories.delete("user-one", record["id"])
    assert memories.search("user-one", "TypeScript") == []
