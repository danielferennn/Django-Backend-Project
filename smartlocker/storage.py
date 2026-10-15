"""Private on-disk storage for sensitive uploads.

``FileSystemStorage`` defaults to world-readable 0644 files and 0755
directories.  Sensitive bytes must not be readable by other users on the host,
so this storage pins:

* files to ``0o640`` (owner read/write, group read, no world access);
* directories, including the storage root, to ``0o750``.

Modes are treated as a ceiling: a stricter existing mode is preserved and a
more permissive one is tightened, so a private file is never left
world-readable.  The whole directory chain from the destination up to the
storage root is clamped (including pre-existing directories) *before* any byte
is written, so an unsecured parent can never expose the file.

No process-wide ``os.umask`` is ever changed: mutating the umask races unrelated
threads and would silently widen public uploads.  Confidentiality instead relies
on the clamped ``0o750`` parent chain (no world traversal) plus Django applying
``0o640`` to the finished file.  A chmod that cannot be applied is a hard
failure, never silently ignored, so a save aborts rather than writing into an
unsecured directory.
"""

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage

# Confidentiality modes for private media: owner read/write, group read only.
PRIVATE_FILE_MODE = 0o640
PRIVATE_DIRECTORY_MODE = 0o750


def _secure_mode(path, ceiling, kind):
    """Clamp ``path`` to ``ceiling`` bits, failing loudly if it cannot be set.

    The target is ``current & ceiling`` so a stricter mode is never widened and
    a more permissive one is tightened.  ``os.chmod`` failures (or a mode that
    is still too permissive afterwards) raise instead of being ignored.
    """
    current = os.stat(path).st_mode & 0o777
    target = current & ceiling
    if target == current:
        return
    try:
        os.chmod(path, target)
    except OSError as exc:
        raise OSError(
            f"could not secure {kind} {path} to {oct(target)} (was {oct(current)}): {exc}"
        ) from exc
    actual = os.stat(path).st_mode & 0o777
    if actual != target:
        raise OSError(
            f"{kind} {path} is mode {oct(actual)}, expected {oct(target)}; refusing to continue."
        )


def secure_private_file(path):
    """Clamp a private file to ``0o640`` (never widening a stricter mode)."""
    _secure_mode(path, PRIVATE_FILE_MODE, 'private file')


def secure_private_directory(path):
    """Clamp a private directory to ``0o750`` (never widening a stricter mode)."""
    _secure_mode(path, PRIVATE_DIRECTORY_MODE, 'private directory')


class PrivateMediaStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('location', settings.PRIVATE_MEDIA_ROOT)
        kwargs.setdefault('file_permissions_mode', PRIVATE_FILE_MODE)
        kwargs.setdefault('directory_permissions_mode', PRIVATE_DIRECTORY_MODE)
        super().__init__(*args, **kwargs)
        self._ensure_private_root()

    def url(self, name):
        raise ValueError('Private files do not have public URLs.')

    def _save(self, name, content):
        # Clamp the destination directory chain (creating any missing levels)
        # before a single byte is written.  Django then applies 0640 to the
        # finished file and raises if that chmod fails, so an unsecured write
        # never completes.  No umask is touched: the 0750 parent chain blocks
        # world traversal for the brief pre-chmod window.
        self._ensure_private_directory(os.path.dirname(self.path(name)))
        return super()._save(name, content)

    def _ensure_private_root(self):
        """Create the storage root if needed and force it to ``0o750``.

        Raises rather than ignoring a chmod failure so an unsecured private root
        can never go unnoticed.
        """
        try:
            os.makedirs(self.location, mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
        except OSError as exc:
            raise OSError(
                f"could not create private media root {self.location}: {exc}"
            ) from exc
        _secure_mode(self.location, PRIVATE_DIRECTORY_MODE, 'private media root')

    def _resolve_private_directory(self, directory):
        """Resolve ``directory`` and require it to be ``self.location`` or below.

        ``os.path.realpath`` collapses every symlink in the path, so a
        pre-existing nested symlink pointing outside the private root resolves
        to its external target.  Comparing the resolved destination against the
        resolved root *before* creating or chmodding anything stops a save from
        escaping the private tree (and from tightening modes on directories the
        process happens to be able to reach).  A destination that resolves to
        neither the root nor a descendant of it is rejected outright.
        """
        root = os.path.realpath(self.location)
        resolved = os.path.realpath(directory)
        if resolved != root and not resolved.startswith(root + os.sep):
            raise OSError(
                f"refusing to use private media directory {directory!r}: it "
                f"resolves to {resolved!r}, outside the private media root "
                f"{root!r}."
            )
        return resolved

    def _ensure_private_directory(self, directory):
        """Create ``directory`` if needed, then clamp it and every ancestor up
        to and including ``self.location`` to ``0o750``.

        Pre-existing directories are tightened too, so a permissive destination
        (or any permissive ancestor below the private root) is never left
        exposing a freshly written file.  The destination is resolved and
        checked against the root first, so a symlink component that would lead
        outside the private tree aborts the save before anything is created or
        chmodded.
        """
        root = os.path.realpath(self.location)
        current = self._resolve_private_directory(directory)
        os.makedirs(current, mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
        while True:
            # Defensive: the resolved path is already symlink-free, but never
            # chmod through a link if one were swapped in after resolution.
            if os.path.islink(current):
                raise OSError(
                    f"refusing to secure {current!r}: it is a symlink, not a "
                    f"private media directory."
                )
            _secure_mode(current, PRIVATE_DIRECTORY_MODE, 'private media directory')
            if os.path.normpath(current) == os.path.normpath(root):
                break
            parent = os.path.dirname(current)
            if parent == current:
                # Defensive: never walk past the filesystem root.
                break
            current = parent


private_media_storage = PrivateMediaStorage()
