from concurrent.futures import ThreadPoolExecutor

import pytest

from omi.library import Library
from omi.models import PresetConfig, Revision, SavePreset
from omi.store import Store


@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    yield Library(store)
    store.close()


def create(library, name="Show A"):
    return library.save(SavePreset(name=name, config=PresetConfig(service="midi")))


def test_empty_case_sensitive_names_and_duplicate(library):
    assert library.list() == []
    create(library, "Default")
    create(library, "default")
    with pytest.raises(RuntimeError):
        create(library, "Default")


def test_concurrent_offline_edits_keep_both_and_resolve(library, tmp_path):
    original = create(library)
    replica_store = Store(tmp_path / "replica.sqlite3")
    replica = Library(replica_store)
    replica.merge(library.export())
    request = dict(preset_id=original.preset_id, parents=[original.id], config=original.config)
    one = library.save(SavePreset(**request, name="Branch A"))
    two = replica.save(SavePreset(**request, name="Branch B"))
    library.merge(replica.export())
    assert {r.id for r in library.heads(original.preset_id)} == {one.id, two.id}
    assert library.list()[0]["conflict"]
    resolved = library.resolve(original.preset_id, two.id)
    replica.merge(library.export())
    assert replica.digest() == library.digest()
    assert replica.heads(original.preset_id) == [resolved]
    assert library.get(original.id) == original  # pinned execution remains available
    replica_store.close()


def test_merge_rejects_collision_bad_parent_and_cycle_atomically(library):
    original = create(library)
    collision = original.model_copy(update={"name": "forged"}).model_dump()
    new = Revision(name="new", config=original.config)
    with pytest.raises(RuntimeError):
        library.merge([new.model_dump(), collision])
    assert len(library.export()) == 1
    bad = Revision(name="bad", config=original.config, parents=["missing"])
    with pytest.raises(ValueError):
        library.merge([bad.model_dump()])
    first = Revision(name="cycle", config=original.config)
    second = Revision(name="cycle", config=original.config, preset_id=first.preset_id, parents=[first.id])
    first.parents = [second.id]
    with pytest.raises(ValueError):
        library.merge([first.model_dump(), second.model_dump()])
    assert len(library.export()) == 1


def test_protected_delete_and_tombstone_replication(library, tmp_path):
    original = create(library)
    with pytest.raises(RuntimeError, match="Pi 2"):
        library.delete(original.preset_id, ["Pi 2 (offline assigned)"])
    deleted = library.delete(original.preset_id, [])
    assert library.list()[0]["deleted"]
    assert library.get(original.id) == original
    assert deleted.parents == [original.id]
    library.merge([original.model_dump()])
    assert library.list()[0]["deleted"]  # stale replica cannot revive by union


def test_parallel_saves_and_reopen_are_consistent(library):
    original = create(library)
    def save(index):
        return library.save(SavePreset(preset_id=original.preset_id, parents=[original.id], name=f"edit{index}", config=original.config))
    with ThreadPoolExecutor(max_workers=8) as executor:
        revisions = list(executor.map(save, range(20)))
    assert len(library.heads(original.preset_id)) == 20
    backup = library.store.backup(library.store.path.with_suffix(".backup"))
    reopened = Store(backup)
    assert Library(reopened).digest() == library.digest()
    assert {r.id for r in revisions}.issubset({r["id"] for r in Library(reopened).export()})
    reopened.close()
