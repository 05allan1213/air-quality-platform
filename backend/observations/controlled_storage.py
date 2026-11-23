"""Pin private storage to a directory descriptor and a persisted generation."""

import errno
import fcntl
import os
import stat
from contextlib import contextmanager
from uuid import UUID, uuid4

_GENERATION = "user.air_quality_storage"


@contextmanager
def directory(root):
    if root.resolve(strict=True) != root:
        raise OSError("storage_directory_unavailable")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise OSError("storage_directory_unavailable")
        confirm_directory(root, fd)
        yield fd
    finally:
        os.close(fd)


def confirm_directory(root, fd):
    if not os.path.samestat(os.fstat(fd), root.lstat()):
        raise OSError("storage_directory_changed")


def volume_generation(fd, *, register=False):
    try:
        marker = os.getxattr(fd, _GENERATION)
    except OSError as exc:
        if exc.errno != errno.ENODATA or not register:
            raise
        try:
            os.setxattr(fd, _GENERATION, uuid4().bytes, os.XATTR_CREATE)
        except OSError as race:
            if race.errno != errno.EEXIST:
                raise
        marker = os.getxattr(fd, _GENERATION)
    if len(marker) != 16:
        raise OSError("storage_generation_invalid")
    if register:
        os.fsync(fd)
    return UUID(bytes=marker)


def register_volume(root):
    with directory(root) as fd:
        generation = volume_generation(fd, register=True)
        confirm_directory(root, fd)
        return generation


@contextmanager
def bound_directory(root, expected):
    with directory(root) as fd:
        confirm_generation(fd, expected)
        yield fd
        confirm_directory(root, fd)
        confirm_generation(fd, expected)


def confirm_generation(fd, expected):
    try:
        if expected is not None and volume_generation(fd) == expected:
            return
    except OSError as exc:
        if exc.errno != errno.ENODATA:
            raise
    raise OSError("storage_generation_mismatch")


def file_entry(directory_fd, name):
    return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)


def confirm_absent(directory_fd, name):
    try:
        file_entry(directory_fd, name)
    except FileNotFoundError:
        return
    raise OSError("file_still_present")


def unlink_from(directory_fd, name):
    # Platform readers hold a shared flock until their descriptors close.
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    except FileNotFoundError:
        # A retry after unlink but before directory fsync must also sync.
        os.fsync(directory_fd)
        confirm_absent(directory_fd, name)
        return
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1:
            raise OSError("uncontrolled_file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not os.path.samestat(st, file_entry(directory_fd, name)):
            raise OSError("changed_file")
        os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        confirm_absent(directory_fd, name)
        if os.fstat(fd).st_nlink:
            raise OSError("file_still_linked")
    finally:
        os.close(fd)
