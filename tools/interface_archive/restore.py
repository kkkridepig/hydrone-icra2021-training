#!/usr/bin/env python3
"""Verify the original LFS archives and restore run artifacts without overwriting data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
ARCHIVES = Path("archives/experiments/2026-09-17")
CHUNK_SIZE = 1024 * 1024


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_parts(name):
    """Reject paths that change meaning on Windows or Unix."""
    trimmed = name[:-1] if name.endswith("/") else name
    parts = trimmed.split("/")
    if not trimmed or "\\" in name or ":" in name or any(ord(c) < 32 for c in name):
        raise ValueError("Unsafe ZIP path: " + repr(name))
    for part in parts:
        if part in ("", ".", "..") or part.endswith((".", " ")):
            raise ValueError("Unsafe ZIP path: " + repr(name))
        if any(c in part for c in '<>"|?*'):
            raise ValueError("Nonportable ZIP path: " + repr(name))
        if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", part.split(".")[0], re.I):
            raise ValueError("Reserved ZIP path: " + repr(name))
    return parts


def member_plan(archive, expected):
    """Validate every member before allowing any destination to be created."""
    seen = {}
    files = {}
    for member in archive.infolist():
        parts = safe_parts(member.filename)
        key = "/".join(parts).casefold()
        if key in seen:
            raise ValueError("Duplicate/case-colliding ZIP member: " + member.filename)
        seen[key] = member.is_dir()
        mode = stat.S_IFMT(member.external_attr >> 16)
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or member.flag_bits & 1:
            raise ValueError("Link/special/encrypted ZIP member: " + member.filename)
        if mode == stat.S_IFDIR and not member.is_dir():
            raise ValueError("ZIP directory type mismatch: " + member.filename)
        if not member.is_dir():
            files[member.filename] = member
    for name in seen:
        parts = name.split("/")
        for count in range(1, len(parts)):
            if seen.get("/".join(parts[:count])) is False:
                raise ValueError("ZIP file/directory collision: " + name)
    if set(files) != set(expected):
        raise ValueError("ZIP file set differs from the committed inventory")
    for name, member in files.items():
        if member.file_size != expected[name]["bytes"]:
            raise ValueError("ZIP member size mismatch: " + name)
    return files


def check_container(path, expected):
    if not path.is_file():
        raise ValueError("Missing archive; run git lfs pull: " + str(path))
    with path.open("rb") as stream:
        if stream.read(64).startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise ValueError("Only a Git LFS pointer is present; run git lfs pull: " + str(path))
    if path.stat().st_size != expected["bytes"] or sha256(path) != expected["sha256"]:
        raise ValueError("Archive size/SHA256 mismatch: " + str(path))


def read_members(path, expected, destination=None, prefix=""):
    with zipfile.ZipFile(path) as archive:
        files = member_plan(archive, expected)
        paths = {}
        if destination is not None:
            for name in files:
                if not name.startswith(prefix):
                    raise ValueError("Member outside artifact prefix: " + name)
                relative = safe_parts(name[len(prefix):])
                paths[name] = destination.joinpath(*relative)
            # mkdir refuses existing directories and symlinks, including empty ones.
            destination.mkdir(parents=True, exist_ok=False)
        for name, member in files.items():
            digest = hashlib.sha256()
            target = None
            try:
                if destination is not None:
                    paths[name].parent.mkdir(parents=True, exist_ok=True)
                    target = paths[name].open("xb")
                with archive.open(member) as source:
                    for block in iter(lambda: source.read(CHUNK_SIZE), b""):
                        digest.update(block)
                        if target is not None:
                            target.write(block)
            finally:
                if target is not None:
                    target.close()
            if digest.hexdigest() != expected[name]["sha256"]:
                raise ValueError("ZIP member SHA256 mismatch: " + name)
    return len(files)


def restore(root, destination=None):
    if destination is not None and os.path.lexists(str(destination)):
        raise ValueError("Destination already exists; choose a new path: " + str(destination))
    manifest = read_json(root / ARCHIVES / "manifest.json")
    inventory = read_json(root / manifest["run_member_inventory"])
    sources = read_json(root / manifest["source_member_inventory"])
    source_members = {item["original_filename"]: item["files"]
                      for item in sources["source_deliveries"].values()}
    archive_specs = manifest["archives"]
    # All original containers must be intact before extraction starts.
    for name, spec in archive_specs.items():
        if safe_parts(name) != [name]:
            raise ValueError("Archive filename must be a single path component")
        check_container(root / ARCHIVES / name, spec)
    source_files = 0
    for name, members in source_members.items():
        source_files += read_members(root / ARCHIVES / name, members)
    count = read_members(root / ARCHIVES / "artifacts.zip", inventory["outer_archive"]["files"],
                         destination, manifest["artifact_member_prefix"])
    return {"archive_count": len(archive_specs), "source_files_verified": source_files,
            "run_files_verified": count, "destination": str(destination) if destination else None,
            "model_files": len(inventory["models"]), "checkpoint_deserialized": False,
            "simulator_build_validated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--verify-only", action="store_true", help="Check archive/member hashes without extracting")
    action.add_argument("--destination", type=Path, help="New artifacts directory; must not exist")
    args = parser.parse_args()
    try:
        result = restore(ROOT, args.destination)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print("Restore failed: " + str(error), file=sys.stderr)
        if args.destination is not None:
            print("Any existing/partially restored directory is retained; do not overwrite it.", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
