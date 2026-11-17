#!/usr/bin/env python3
"""Extract Electron ASAR resources without installing build tools at runtime.

Format: Electron's chromium-pickle header followed by file data. Unpacked files
live beside app.asar in app.asar.unpacked. Verify per-file integrity when present.
See https://github.com/electron/asar/blob/v3.4.1/src/disk.ts .
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import sys


def extract(archive_path: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with archive_path.open("rb") as archive:
        prefix = archive.read(16)
        if len(prefix) != 16:
            raise ValueError("Truncated ASAR header")
        pickle_length, header_size, header_pickle_size, json_size = struct.unpack("<IIII", prefix)
        if pickle_length != 4 or not 8 <= header_size <= 64 * 1024 * 1024 or json_size > header_size - 8:
            raise ValueError("Invalid ASAR header sizes")
        header = json.loads(archive.read(json_size))
        data_start = 8 + header_size
        links = []

        def walk(directory, parent=PurePosixPath()):
            for name, entry in directory.get("files", {}).items():
                if name in ("", ".", "..") or "/" in name or "\\" in name:
                    raise ValueError("Unsafe ASAR entry")
                relative = parent / name
                target = root.joinpath(*relative.parts)
                if "files" in entry:
                    target.mkdir(exist_ok=True)
                    walk(entry, relative)
                elif "link" in entry:
                    link = PurePosixPath(entry["link"])
                    if link.is_absolute() or ".." in link.parts:
                        raise ValueError("Unsafe ASAR symlink")
                    links.append((target, root.joinpath(*link.parts)))
                else:
                    size = int(entry["size"])
                    if size < 0:
                        raise ValueError("Invalid ASAR file size")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if entry.get("unpacked"):
                        source = Path(str(archive_path) + ".unpacked").joinpath(*relative.parts)
                        if not source.resolve().is_relative_to(Path(str(archive_path) + ".unpacked").resolve()):
                            raise ValueError("Unsafe unpacked ASAR path")
                        shutil.copyfile(source, target)
                    else:
                        offset = int(entry["offset"])
                        if offset < 0:
                            raise ValueError("Invalid ASAR file offset")
                        archive.seek(data_start + offset)
                        remaining = size
                        with target.open("wb") as output:
                            while remaining:
                                data = archive.read(min(1024 * 1024, remaining))
                                if not data:
                                    raise ValueError("Truncated ASAR file")
                                output.write(data)
                                remaining -= len(data)
                    if target.stat().st_size != size:
                        raise ValueError("ASAR size mismatch: " + str(relative))
                    integrity = entry.get("integrity", {})
                    if integrity.get("algorithm") == "SHA256":
                        with target.open("rb") as handle:
                            if hashlib.file_digest(handle, "sha256").hexdigest() != integrity["hash"]:
                                raise ValueError("ASAR checksum mismatch: " + str(relative))
                    if entry.get("executable"):
                        target.chmod(0o755)

        walk(header)
        # Copy link contents: package archives need no writeable symlinks.
        for target, source in links:
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copyfile(source, target)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: extract-asar.py app.asar destination")
    extract(Path(sys.argv[1]), Path(sys.argv[2]))
