"""Identity data for a lock file created by a guarded, disposable runner."""

from pathlib import Path


def lock_identity(info):
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return f"{boot}:{info.st_dev}:{info.st_ino}:{info.st_uid}"
