"""Build the additive H packet from reviewed documents only. Output must be a new path."""

import argparse
import hashlib
import os
import sys
from pathlib import Path
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from s07_handoff import add_member, read_regular  # noqa: E402

# Do not walk tmp/, evidence directories, the replay tree, or a caller-supplied file list.
DOCUMENTS = ("S07.md", "S07_LEGACY_LEDGER.md")
SUPPLEMENT = (
    "source.json",
    "RIGHTS.md",
    "REVIEW_HANDOFF.md",
    "review.md",
    "evidence.json",
    "receipt_index.py",
    "package.py",
    "replay/h_case.py",
    "replay/h_compose.py",
    "replay/h_host.py",
    "replay/h_restore.py",
    "replay/h_restore_case.py",
    "replay/h_restore_custody.py",
)


def build(destination):
    members = sorted(
        [ROOT / "docs/operations/S07_handoff.md"]
        + [HERE.parent / name for name in DOCUMENTS]
        + [HERE / name for name in SUPPLEMENT]
    )
    checksums = []
    with ZipFile(destination, "x") as archive:
        for source in members:
            name = source.relative_to(ROOT).as_posix()
            data = read_regular(source)
            add_member(archive, name, "100644", data)
            checksums.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
        add_member(archive, "SHA256SUMS", "100644", "".join(checksums).encode())
    with destination.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_zip", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    print(f"{build(args.new_zip)}  {args.new_zip.name}")
