#!/usr/bin/env python3
"""Exercise S07 handoff against an isolated Git repository and ZIP fixtures."""

import json
import stat
import subprocess
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from s06_guard import IsolationError
from s07_handoff import (
    MANIFEST,
    REQUIRED,
    add_member,
    build,
    checkout,
    inspect_archive,
    source_digest,
    source_files,
)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix="s07-handoff-test-")))
        self.git("init", "--quiet")
        self.git("config", "user.name", "S07 Fixture")
        self.git("config", "user.email", "s07@example.invalid")
        self.git("config", "core.hooksPath", "/dev/null")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.filemode", "true")
        for name in REQUIRED | {"docs/design/总体实施方案.md", ".gitignore"}:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"frozen fixture\n" if name != ".gitignore" else b"tmp/\n")
        (self.root / "main.py").chmod(0o755)
        self.fixture_secrets = (
            ".env",
            "backend/samples/data/historical.csv",
            "backend/accounts/private/secret.py",
            "docker/reviewed.key",
            "docs/verification/S06_evidence.json",
            "docs/verification/S07-raw.json",
        )
        for name in self.fixture_secrets:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"private fixture marker\n")
        self.git("add", "--", *sorted(REQUIRED), "docs/design", ".gitignore", *self.fixture_secrets)
        self.git("commit", "--quiet", "-m", "Disposable S07 handoff fixture")
        self.commit = self.git("rev-parse", "HEAD").strip().decode()
        self.source = source_digest(self.root)
        self.receipt = build(self.root, self.commit)
        self.archive = self.root / self.receipt["archive"]

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, timeout=15)

    def test_explicit_commit_frozen_with_working_tree_changes(self):
        (self.root / "main.py").write_bytes(b"working tree change\n")
        self.git("add", "--", "main.py")
        self.git("commit", "--quiet", "-m", "Different latest fixture tree")
        (self.root / "main.py").write_bytes(b"second working tree change\n")
        (self.root / "backend/observations").mkdir(exist_ok=True)
        extra = Path("backend/observations/extra_case.py")
        (self.root / extra).write_bytes(b"untracked fixture\n")
        self.assertIn(extra, source_files(self.root))
        self.assertNotEqual(source_digest(self.root), self.source)
        result = build(self.root, self.commit)
        self.assertEqual(result["archive_sha256"], self.receipt["archive_sha256"])
        with ZipFile(self.root / result["archive"]) as archive:
            self.assertEqual(archive.read("main.py"), b"frozen fixture\n")
            self.assertTrue(set(archive.namelist()).isdisjoint(self.fixture_secrets))
            self.assertNotIn(extra.as_posix(), archive.namelist())
            self.assertTrue(all(not name.startswith(".git/") for name in archive.namelist()))

    def test_clean_extract_nested_under_another_git_checkout(self):
        destination = self.root / "tmp/rebuild"
        result = inspect_archive(self.archive, destination)
        self.assertEqual(result["extracted_source_sha256"], self.source)
        self.assertEqual(
            checkout(destination), {"head": self.commit, "dirty_paths": 0, "origin": "handoff"}
        )
        self.assertEqual(stat.S_IMODE((destination / "main.py").stat().st_mode), 0o755)
        self.assertTrue((destination / "docs/design/总体实施方案.md").is_file())
        self.assertFalse((destination / ".git").exists())
        (destination / "main.py").write_bytes(b"tampered bytes\n")
        with self.assertRaisesRegex(IsolationError, "handoff_member_digest_mismatch"):
            source_digest(destination)

    def test_refuse_build_from_an_unpinned_ref(self):
        with self.assertRaisesRegex(IsolationError, "handoff_full_commit_required"):
            build(self.root, "HEAD")

    def test_refuse_git_inventory_from_parent_without_manifest(self):
        unsealed = self.root / "tmp/unsealed"
        unsealed.mkdir()
        with self.assertRaises(FileNotFoundError):
            source_files(unsealed)

    def test_refuse_existing_destination_and_preserve_its_contents(self):
        destination = self.root / "tmp/existing"
        destination.mkdir()
        observed = destination / "keep"
        observed.write_bytes(b"existing file\n")
        with self.assertRaises(FileExistsError):
            inspect_archive(self.archive, destination)
        self.assertEqual(list(destination.iterdir()), [observed])
        self.assertEqual(observed.read_bytes(), b"existing file\n")

    def test_refuse_unlisted_runtime_source(self):
        destination = self.root / "tmp/expanded"
        inspect_archive(self.archive, destination)
        (destination / "scripts/runtime_override.py").write_bytes(b"unlisted fixture\n")
        with self.assertRaisesRegex(IsolationError, "handoff_unlisted_runtime_file"):
            checkout(destination)

    def rewrite(self, name, *, addition=None, change=None):
        variant = self.root / "tmp" / name
        with ZipFile(self.archive) as source, ZipFile(variant, "x") as target:
            for member in source.infolist():
                data = source.read(member)
                mode = format(member.external_attr >> 16, "o")
                if change is not None:
                    mode, data = change(member.filename, mode, data)
                add_member(target, member.filename, mode, data)
            if addition:
                add_member(target, *addition)
        return variant

    def rejects(self, archive, error):
        destination = self.root / "tmp/rejected"
        with self.assertRaisesRegex(IsolationError, error):
            inspect_archive(archive, destination)
        self.assertFalse(destination.exists())

    def test_reject_duplicate_zip_member(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            variant = self.rewrite("duplicate.zip", addition=("main.py", "100755", b"duplicate"))
        self.rejects(variant, "handoff_duplicate_member")

    def test_reject_zip_member_outside_whitelist(self):
        for index, name in enumerate(
            ("../escape.py", "/main.py", "scripts/../../escape.py", ".env")
        ):
            with self.subTest(name=name):
                variant = self.rewrite(f"out-{index}.zip", addition=(name, "100644", b"x"))
                self.rejects(variant, "handoff_disallowed_member")

    def test_reject_zip_symlink_and_git_symlink(self):
        variant = self.rewrite(
            "symlink.zip",
            change=lambda name, mode, data: (
                ("120777", b".env") if name == "main.py" else (mode, data)
            ),
        )
        self.rejects(variant, "handoff_archive_member_mode")
        (self.root / "scripts/leak.py").symlink_to("../.env")
        self.git("add", "--", "scripts/leak.py")
        self.git("commit", "--quiet", "-m", "Disposable unsafe mode fixture")
        with self.assertRaisesRegex(IsolationError, "handoff_git_non_regular_member"):
            build(self.root, self.git("rev-parse", "HEAD").strip().decode())

    def test_reject_payload_with_valid_crc_but_wrong_digest(self):
        variant = self.rewrite(
            "mismatch.zip",
            change=lambda name, mode, data: (
                (mode, b"tampered bytes\n") if name == "main.py" else (mode, data)
            ),
        )
        with ZipFile(variant) as archive:
            self.assertIsNone(archive.testzip())
        self.rejects(variant, "handoff_member_digest_mismatch")

    def test_reject_manifest_member_set_mismatch(self):
        def remove_member(name, mode, data):
            if name == MANIFEST:
                value = json.loads(data)
                value["files"] = [
                    entry for entry in value["files"] if entry["path"] != ".gitignore"
                ]
                data = json.dumps(value).encode()
            return mode, data

        self.rejects(
            self.rewrite("inventory.zip", change=remove_member), "handoff_member_set_mismatch"
        )

    def test_reject_invalid_central_directory_crc(self):
        variant = self.root / "tmp/crc.zip"
        with ZipFile(self.archive) as source:
            index = source.start_dir + 16
        modified = bytearray(self.archive.read_bytes())
        modified[index] ^= 1
        variant.write_bytes(modified)
        self.rejects(variant, "handoff_invalid_crc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
