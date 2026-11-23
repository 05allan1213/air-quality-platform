#!/usr/bin/env python3
"""Create, audit, and extract an S07 source handoff from local Git objects."""

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zlib
from pathlib import Path
from tempfile import mkdtemp
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from s06_guard import IsolationError, require

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "S07_HANDOFF_MANIFEST.json"
GIT_MODES = {"100644", "100755"}
COMMIT_RE = r"[0-9a-f]{40}(?:[0-9a-f]{24})?"
MAX_FILE_SIZE = 16 * 1024 * 1024
MAX_TOTAL_SIZE = 64 * 1024 * 1024
MAX_FILES = 1600
SOURCE_FILES = {
    ".dockerignore",
    ".env.example",
    ".python-version",
    "Dockerfile",
    "main.py",
    "pyproject.toml",
    "uv.lock",
    "compose.yaml",
    "compose.cnemc.yaml",
    "compose.cnemc.once.yaml",
    "compose.host-infra.yaml",
    "compose.offline.yaml",
    "compose.wsgi.yaml",
    "scripts/s03_compose.yaml",
    "scripts/s07_isolation.yaml",
    "scripts/s07_offline_dns.yaml",
    "docker/10-app-role.sql",
    "docker/cnemc-egress.cfg",
    "docs/api/openapi.v1.json",
    "backend/accounts/templates/admin/account_controls.html",
    "backend/samples/data/co-hour-v1.json",
}
PUBLIC_DOCUMENTS = {
    ".gitignore",
    "AGENTS.md",
    "README.md",
    "docs/api/v1.md",
    "docs/design/城市空气质量监测数据分析平台_系统总体设计方案_V3.0.md",
    "docs/design/总体实施方案.md",
    "docs/operations/S07.md",
    "docs/operations/S07_handoff.md",
    "docs/operations/S07_T09.md",
    "docs/plans/S07_IMPLEMENTATION_PLAN.md",
    "docs/verification/S07.md",
    "docs/verification/S07_LEGACY_LEDGER.md",
    "docs/verification/S07_PRECHECK.md",
    "docs/verification/S07_T09.json",
    "docs/verification/S07_T09.md",
    "docs/verification/S07_V04.json",
    "docs/verification/S07_V12.json",
    "docs/verification/S07_V15.json",
    "docs/verification/S07_evidence.args",
    "docs/verification/S07_evidence.json",
    "docs/verification/S07_reviews/l3-review.md",
    "docs/verification/S07_reviews/recheck.md",
}
REQUIRED = {
    ".env.example",
    "Dockerfile",
    "main.py",
    "compose.yaml",
    "compose.host-infra.yaml",
    "compose.offline.yaml",
    "pyproject.toml",
    "uv.lock",
    "backend/manage.py",
    "backend/samples/data/co-hour-v1.json",
    "docs/api/openapi.v1.json",
    "scripts/s07_handoff.py",
    "scripts/s07_isolation.yaml",
    "scripts/s07_t09.py",
    "scripts/verify_s07.py",
    "scripts/verify_s07_host.py",
}
PYTHON_SOURCE = re.compile(
    r"(?:backend/(?:manage|(?:accounts|collection|config|observations|samples)/"
    r"(?:(?:adapters|management|commands|migrations)/)*[A-Za-z0-9_]+)"
    r"|scripts/[A-Za-z0-9_]+|tests/(?:collection/)?[A-Za-z0-9_]+)\.py"
)


def source_member(name):
    """Source layout and explicit non-Python assets checked by the S07 runners."""
    return name in SOURCE_FILES or bool(PYTHON_SOURCE.fullmatch(name))


def bundle_member(name):
    return name in PUBLIC_DOCUMENTS or source_member(name)


def invoke_git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "--no-replace-objects", *args],
        cwd=root,
        env=env | {"GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True,
        timeout=30,
        check=False,
    )


def git_bytes(root, *args):
    result = invoke_git(root, *args)
    require(result.returncode == 0, "handoff_git_command_failed")
    return result.stdout


def own_checkout(root):
    if not (root / ".git").exists():
        return False
    result = invoke_git(root, "rev-parse", "--show-toplevel")
    require(
        result.returncode == 0 and Path(os.fsdecode(result.stdout.rstrip(b"\n"))) == root,
        "handoff_project_git_root_mismatch",
    )
    return True


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def parse_manifest(raw):
    require(len(raw) <= MAX_FILE_SIZE, "handoff_manifest_size")
    value = json.loads(raw)
    require(
        isinstance(value, dict)
        and set(value) == {"version", "commit", "files"}
        and type(value["version"]) is int
        and value["version"] == 1
        and isinstance(value["commit"], str)
        and re.fullmatch(COMMIT_RE, value["commit"])
        and isinstance(value["files"], list)
        and 1 <= len(value["files"]) <= MAX_FILES,
        "handoff_manifest_format",
    )
    entries = {}
    for entry in value["files"]:
        require(
            isinstance(entry, dict)
            and set(entry) == {"path", "size", "sha256", "mode"}
            and isinstance(entry["path"], str)
            and bundle_member(entry["path"])
            and entry["path"] not in entries
            and type(entry["size"]) is int
            and 0 <= entry["size"] <= MAX_FILE_SIZE
            and isinstance(entry["sha256"], str)
            and re.fullmatch(r"[a-f0-9]{64}", entry["sha256"])
            and isinstance(entry["mode"], str)
            and entry["mode"] in GIT_MODES,
            "handoff_manifest_entry",
        )
        entries[entry["path"]] = entry
    require(REQUIRED <= entries.keys(), "handoff_missing_runtime_files")
    require(sum(entry["size"] for entry in entries.values()) <= MAX_TOTAL_SIZE, "handoff_size")
    return value["commit"], entries


def read_regular(path):
    require(
        path.resolve(strict=True) == path and stat.S_ISREG(path.lstat().st_mode),
        "unexpected_source",
    )
    require(path.stat().st_size <= MAX_FILE_SIZE, "handoff_member_size")
    return path.read_bytes()


def read_tree_manifest(root):
    return parse_manifest(read_regular(root / MANIFEST))


def checkout(root):
    """Report an owned Git checkout or the verified identity of a handoff."""
    root = root.resolve(strict=True)
    if own_checkout(root):
        return {
            "head": git_bytes(root, "rev-parse", "HEAD").decode().strip(),
            "dirty_paths": len(git_bytes(root, "status", "--porcelain", "-uall").splitlines()),
            "origin": "git",
        }
    commit, entries = read_tree_manifest(root)
    verify_tree(root, entries)
    return {"head": commit, "dirty_paths": 0, "origin": "handoff"}


def source_files(root):
    root = root.resolve(strict=True)
    if own_checkout(root):
        names = git_bytes(
            root,
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            "backend",
            "docker",
            "scripts",
            "tests",
            "docs/api/openapi.v1.json",
            *sorted(name for name in SOURCE_FILES if "/" not in name),
        ).split(b"\0")
        return [Path(name) for name in sorted(set(map(os.fsdecode, names))) if source_member(name)]
    _, entries = read_tree_manifest(root)
    verify_tree(root, entries)
    return [Path(name) for name in sorted(entries) if source_member(name)]


def source_digest(root, files=None):
    """Hash the same selected runtime bytes in A, B, and a clean export."""
    root = root.resolve(strict=True)
    digest = hashlib.sha256()
    for name in source_files(root) if files is None else files:
        digest.update(os.fsencode(name) + b"\0" + read_regular(root / name) + b"\0")
    return digest.hexdigest()


def member_matches(data, entry):
    require(len(data) == entry["size"], "handoff_member_size_mismatch")
    require(sha256(data) == entry["sha256"], "handoff_member_digest_mismatch")


def verify_tree(root, entries):
    for name, entry in entries.items():
        path = root / name
        member_matches(read_regular(path), entry)
        require(
            stat.S_IMODE(path.stat().st_mode) == int(entry["mode"], 8) & 0o777,
            "handoff_member_mode_mismatch",
        )
    # A generated venv or run receipt is outside the sealed source layout.
    runtime = {name for name in SOURCE_FILES if os.path.lexists(root / name)}
    for folder in ("backend", "scripts", "tests"):
        runtime.update(
            name.as_posix()
            for path in (root / folder).rglob("*.py")
            if source_member((name := path.relative_to(root)).as_posix())
        )
    require(runtime <= entries.keys(), "handoff_unlisted_runtime_file")


def commit_files(root, commit):
    require(re.fullmatch(COMMIT_RE, commit), "handoff_full_commit_required")
    require(own_checkout(root), "handoff_project_git_root_required")
    resolved = git_bytes(root, "rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}")
    require(resolved.decode().strip() == commit, "handoff_commit_required")
    files = {}
    for record in git_bytes(root, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not record:
            continue
        meta, name = record.split(b"\t", 1)
        path = os.fsdecode(name)
        if bundle_member(path):
            mode, kind, oid = meta.decode().split()
            require(mode in GIT_MODES and kind == "blob", "handoff_git_non_regular_member")
            files[path] = (mode, oid)
    require(REQUIRED <= files.keys(), "handoff_missing_runtime_files")
    return files


def add_member(archive, name, mode, data):
    # Fixed timestamp and Unix mode make two archives from one commit reproducible.
    item = ZipInfo(name)
    item.create_system = 3
    item.external_attr = int(mode, 8) << 16
    archive.writestr(item, data, compress_type=ZIP_DEFLATED)


def create_archive(root, commit, destination, files):
    with ZipFile(destination, "x") as archive:
        entries = []
        for name, (mode, oid) in sorted(files.items()):
            size = int(git_bytes(root, "cat-file", "-s", oid))
            require(size <= MAX_FILE_SIZE, "handoff_member_size")
            data = git_bytes(root, "cat-file", "blob", oid)
            require(len(data) == size, "handoff_git_blob_size")
            entries.append({"path": name, "size": size, "sha256": sha256(data), "mode": mode})
            add_member(archive, name, mode, data)
        require(sum(entry["size"] for entry in entries) <= MAX_TOTAL_SIZE, "handoff_size")
        raw = json_bytes({"version": 1, "commit": commit, "files": entries})
        add_member(archive, MANIFEST, "100644", raw)


def audit_archive(archive):
    members = archive.infolist()
    require(1 <= len(members) <= MAX_FILES + 1, "handoff_member_count")
    names = {member.filename for member in members}
    require(len(names) == len(members), "handoff_duplicate_member")
    for member in members:
        require(
            member.orig_filename == member.filename
            and (member.filename == MANIFEST or bundle_member(member.filename)),
            "handoff_disallowed_member",
        )
        require(
            member.create_system == 3 and format(member.external_attr >> 16, "o") in GIT_MODES,
            "handoff_archive_member_mode",
        )
        require(member.file_size <= MAX_FILE_SIZE, "handoff_member_size")
    require(sum(member.file_size for member in members) <= MAX_TOTAL_SIZE, "handoff_size")
    require(MANIFEST in names, "handoff_missing_manifest")
    require(archive.testzip() is None, "handoff_invalid_crc")
    raw = archive.read(MANIFEST)
    commit, entries = parse_manifest(raw)
    require(names == entries.keys() | {MANIFEST}, "handoff_member_set_mismatch")
    for name, entry in entries.items():
        require(
            archive.getinfo(name).external_attr >> 16 == int(entry["mode"], 8),
            "handoff_member_mode_mismatch",
        )
        member_matches(archive.read(name), entry)
    return entries, {
        "status": "PASS",
        "commit": commit,
        "files": len(entries),
        "manifest_sha256": sha256(raw),
    }


def inspect_archive(archive_path, destination=None):
    with archive_path.open("rb") as path_file, ZipFile(path_file) as archive:
        entries, receipt = audit_archive(archive)
        path_file.seek(0)
        receipt["archive_sha256"] = hashlib.file_digest(path_file, "sha256").hexdigest()
        if destination is not None:
            receipt["extracted_source_sha256"] = extract_tree(
                archive, destination, entries, receipt
            )
        return receipt


def extract_tree(archive, destination, entries, receipt):
    destination = destination.absolute()
    require(destination.parent.resolve(strict=True) == destination.parent, "handoff_extract_parent")
    destination.mkdir(mode=0o700)
    try:
        for name in [MANIFEST, *entries]:
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(name)
            with path.open("xb") as stream:
                stream.write(data)
            mode = "100644" if name == MANIFEST else entries[name]["mode"]
            path.chmod(int(mode, 8) & 0o777)
        require(
            sha256(read_regular(destination / MANIFEST)) == receipt["manifest_sha256"],
            "handoff_extract_manifest_changed",
        )
        return source_digest(destination)
    except BaseException:
        shutil.rmtree(destination)
        raise


def build(root, commit):
    root = root.resolve(strict=True)
    files = commit_files(root, commit)
    parent = root / "tmp" / "s07-handoff"
    for directory in (parent.parent, parent):
        directory.mkdir(mode=0o700, exist_ok=True)
        require(
            directory.resolve(strict=True) == directory
            and directory.is_dir()
            and directory.stat().st_uid == os.geteuid()
            and not directory.stat().st_mode & 0o022,
            "handoff_unsafe_output_parent",
        )
    run = Path(mkdtemp(prefix=f"{commit[:12]}-", dir=parent))
    archive = run / "S07.zip"
    create_archive(root, commit, archive, files)
    receipt = inspect_archive(archive)
    receipt["archive"] = str(archive.relative_to(root))
    (run / "verification.json").write_bytes(json_bytes(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("build").add_argument("--commit", required=True)
    commands.add_parser("verify").add_argument("archive", type=Path)
    extract = commands.add_parser("extract")
    extract.add_argument("archive", type=Path)
    extract.add_argument("destination", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "build":
            receipt = build(ROOT, args.commit)
        else:
            receipt = inspect_archive(args.archive, getattr(args, "destination", None))
    except (
        BadZipFile,
        OSError,
        subprocess.SubprocessError,
        ValueError,
        RuntimeError,
        zlib.error,
    ) as exc:
        error = str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
        sys.stderr.buffer.write(json_bytes({"status": "FAIL", "error": error}))
        return 1
    sys.stdout.buffer.write(json_bytes(receipt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
