"""Immutable preset revisions; union replication with explicit DAG conflicts."""
from __future__ import annotations

import hashlib
import json

from .models import Revision, SavePreset
from .store import Store


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class Library:
    def __init__(self, store: Store):
        self.store = store

    def export(self) -> list[dict]:
        with self.store.lock:
            return [json.loads(row[0]) for row in self.store.connection.execute("SELECT value FROM revisions ORDER BY id")]

    def digest(self) -> str:
        return hashlib.sha256(canonical({"revisions": self.export()}).encode()).hexdigest()

    def get(self, revision_id: str) -> Revision:
        with self.store.lock:
            row = self.store.connection.execute("SELECT value FROM revisions WHERE id=?", (revision_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown revision: {revision_id}")
            return Revision.model_validate_json(row[0])

    def heads(self, preset_id: str) -> list[Revision]:
        with self.store.lock:
            revisions = [Revision.model_validate_json(row[0]) for row in self.store.connection.execute("SELECT value FROM revisions WHERE preset_id=?", (preset_id,))]
        parents = {parent for revision in revisions for parent in revision.parents}
        return sorted((r for r in revisions if r.id not in parents), key=lambda r: r.id)

    def list(self) -> list[dict]:
        with self.store.lock:
            preset_ids = [row[0] for row in self.store.connection.execute("SELECT DISTINCT preset_id FROM revisions")]
        result = []
        for preset_id in preset_ids:
            heads = self.heads(preset_id)
            if not heads:
                continue
            revision = heads[0]
            result.append({"preset_id": preset_id, "name": revision.name,
                           "heads": [r.id for r in heads], "conflict": len(heads) > 1,
                           "deleted": all(r.deleted for r in heads), "revision": revision.model_dump()})
        return sorted(result, key=lambda row: (row["name"], row["preset_id"]))

    def merge(self, revisions: list[dict]) -> dict:
        parsed = [Revision.model_validate(value) for value in revisions]
        with self.store.transaction() as connection:
            all_revisions = {r.id: r for r in (Revision.model_validate_json(row[0]) for row in connection.execute("SELECT value FROM revisions"))}
            added: list[Revision] = []
            for revision in parsed:
                previous = all_revisions.get(revision.id)
                if previous and canonical(previous.model_dump()) != canonical(revision.model_dump()):
                    raise RuntimeError(f"Revision ID collision: {revision.id}")
                if previous is None:
                    added.append(revision)
                    all_revisions[revision.id] = revision
            for revision in added:
                if len(revision.parents) != len(set(revision.parents)):
                    raise ValueError("Duplicate revision parents")
                for parent_id in revision.parents:
                    parent = all_revisions.get(parent_id)
                    if parent is None or parent.preset_id != revision.preset_id:
                        raise ValueError(f"Missing or foreign parent: {parent_id}")
            # Iterative DAG validation avoids recursion limits on long-running libraries.
            visited: set[str] = set()
            visiting: set[str] = set()
            for root in added:
                stack = [(root.id, False)]
                while stack:
                    revision_id, exiting = stack.pop()
                    if exiting:
                        visiting.remove(revision_id)
                        visited.add(revision_id)
                        continue
                    if revision_id in visited:
                        continue
                    if revision_id in visiting:
                        raise ValueError("Revision ancestry contains a cycle")
                    visiting.add(revision_id)
                    stack.append((revision_id, True))
                    stack.extend((parent_id, False) for parent_id in all_revisions[revision_id].parents)
            connection.executemany("INSERT INTO revisions VALUES (?,?,?)", [(r.id, r.preset_id, canonical(r.model_dump())) for r in added])
        return {"added": len(added), "digest": self.digest(), "conflicts": [row["preset_id"] for row in self.list() if row["conflict"]]}

    def save(self, request: SavePreset, author: str = "local") -> Revision:
        with self.store.lock:
            if request.preset_id:
                existing = self.heads(request.preset_id)
                if not existing:
                    raise KeyError(f"Unknown preset: {request.preset_id}")
                if not request.parents:
                    raise ValueError("An edit must identify its base revision")
            elif request.parents:
                raise ValueError("A new preset cannot have parents")
            if any(row["name"] == request.name and row["preset_id"] != request.preset_id and not row["deleted"] for row in self.list()):
                raise RuntimeError("A preset with that exact name already exists")
            arguments = {"name": request.name, "config": request.config, "parents": request.parents, "author": author}
            if request.preset_id:
                arguments["preset_id"] = request.preset_id
            revision = Revision(**arguments)
            self.merge([revision.model_dump()])
            return revision

    def resolve(self, preset_id: str, chosen_revision: str, author: str = "local") -> Revision:
        with self.store.lock:
            heads = self.heads(preset_id)
            if chosen_revision not in {r.id for r in heads}:
                raise ValueError("Choose one of the conflicting heads")
            chosen = self.get(chosen_revision)
            revision = Revision(preset_id=preset_id, parents=[r.id for r in heads], name=chosen.name,
                                config=chosen.config, deleted=chosen.deleted, author=author)
            self.merge([revision.model_dump()])
            return revision

    def delete(self, preset_id: str, used_by: list[str]) -> Revision:
        if used_by:
            raise RuntimeError("Preset in use or assigned: " + ", ".join(used_by))
        with self.store.lock:
            heads = self.heads(preset_id)
            if not heads:
                raise KeyError(preset_id)
            if len(heads) > 1:
                raise RuntimeError("Resolve the preset conflict before deleting it")
            revision = Revision(preset_id=preset_id, parents=[r.id for r in heads], name=heads[0].name,
                                config=heads[0].config, deleted=True)
            self.merge([revision.model_dump()])
            return revision
