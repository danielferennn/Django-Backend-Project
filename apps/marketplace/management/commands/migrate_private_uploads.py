"""One-time migration of sensitive uploads into private storage.

Two classes of bytes are moved out of the public ``settings.MEDIA_ROOT`` into
``settings.PRIVATE_MEDIA_ROOT`` without renaming anything and without touching a
single database row:

Referenced uploads
    ``Transaction.payment_proof``, ``Datawajahnew.image_user`` and
    ``Logsmartaccess2.image`` used to be written under the public
    ``settings.MEDIA_ROOT``.  Their fields now use
    ``smartlocker.storage.private_media_storage`` (backed by
    ``settings.PRIVATE_MEDIA_ROOT``), but schema migrations only change where
    *new* files are written: existing rows still hold the original stored names
    and the bytes still live in the public tree.

Orphaned uploads
    Legacy public directories that held sensitive files whose field or row has
    since been removed.  Every regular file below
    ``MEDIA_ROOT/payment_proofs/``, ``MEDIA_ROOT/imagetraining/``,
    ``MEDIA_ROOT/tracking/`` and the legacy ``MEDIA_ROOT/qris_images/`` is
    migrated too, even when no row references it any more.  The path relative to
    ``MEDIA_ROOT`` is preserved verbatim under ``PRIVATE_MEDIA_ROOT``.

For every file the command:

1. resolves the public source under ``MEDIA_ROOT`` and the private destination
   under ``PRIVATE_MEDIA_ROOT``, refusing any name that escapes its root;
2. fails loudly on a missing source, a non-regular source, an escaping or
   symlinked path, or a destination that exists but is not byte-identical to the
   source;
3. otherwise streams the bytes into private storage, verifies the copy is
   byte-identical, and only then deletes the public source so sensitive data is
   not left publicly served from ``MEDIA_ROOT``.

The preflight is all-or-nothing: every source is validated and every destination
conflict is detected before a single byte is copied or deleted.  If any file
fails validation nothing is copied or deleted and the command exits non-zero.

Ordering (the command refuses to run while a field still writes to MEDIA_ROOT)::

    python manage.py migrate                     # fields now point at private storage
    python manage.py migrate_private_uploads --dry-run
    python manage.py migrate_private_uploads

``--dry-run`` reports the referenced/orphaned split and every count without
writing or deleting anything.
"""

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.management.base import BaseCommand, CommandError
from django.db import models

from smartlocker.storage import (
    PRIVATE_DIRECTORY_MODE,
    secure_private_directory,
    secure_private_file,
)

# Bounded streaming so large uploads are never read into memory at once.
CHUNK_SIZE = 64 * 1024

# (app_label, model_name, file field) whose referenced bytes must be private.
TARGETS = (
    ("marketplace", "Transaction", "payment_proof"),
    ("facerecognition", "Datawajahnew", "image_user"),
    ("facerecognition", "Logsmartaccess2", "image"),
)

# Legacy public directories that may still hold sensitive bytes whose field or
# row has since been removed.  Every regular file below these roots is migrated
# (relative path preserved), even when no database row references it.
ORPHAN_SWEEP_ROOTS = (
    "payment_proofs",
    "imagetraining",
    "tracking",
    "qris_images",
)

# Only used to label work items; nothing is keyed off these strings.
REFERENCED = "referenced"
ORPHANED = "orphaned"


@dataclass(frozen=True)
class Upload:
    """A single stored file, its public source and its private destination."""

    label: str
    name: str
    source: Path
    destination: Path
    kind: str = REFERENCED


def _sha256(path: Path) -> bytes:
    """Hash a file in bounded chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.digest()

def _harden_file(path: Path) -> None:
    """Clamp a private file to ``0o640`` (never widening, never ignoring errors)."""
    secure_private_file(path)

def _harden_directory(path: Path) -> None:
    """Clamp a private directory to ``0o750`` (never widening, never ignoring)."""
    secure_private_directory(path)

def _ensure_private_dirs(path: Path) -> None:
    """Create ``path`` and parents, forcing every level to ``0o750``.

    No process-wide umask is used; ``mkdir(mode=0o750)`` can only be narrowed by
    an unrelated umask and never widened, and every level (including the target
    when it already exists) is clamped explicitly afterwards.
    """
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    os.makedirs(path, mode=PRIVATE_DIRECTORY_MODE, exist_ok=True)
    for directory in (*missing, path):
        _harden_directory(directory)

def _harden_dir_chain(directory: Path, root: Path) -> None:
    """Tighten ``directory`` and every parent up to and including ``root``."""
    root = root.resolve()
    current = directory.resolve()
    while current == root or root in current.parents:
        _harden_directory(current)
        if current == root:
            break
        current = current.parent

def _copy_verified(source: Path, destination: Path) -> str:
    """Stream ``source`` into ``destination`` and prove the copy is identical.

    Returns the SHA-256 hex digest of the bytes.  The destination is written to
    a temporary sibling and atomically renamed, so a partially written file can
    never be mistaken for a completed migration.  The temporary file is created
    with ``os.open(..., 0o640)`` (mode bits are never widened by an unrelated
    umask) and clamped before writing, and the destination (plus its directory
    chain) is clamped to ``0o640``/``0o750`` before the public source may be
    deleted.  Any chmod that cannot be applied raises, so the caller never
    deletes the public source for an unsecured private copy.
    """
    _ensure_private_dirs(destination.parent)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.part"
    )
    digest = hashlib.sha256()
    try:
        # 0o640 is group-read at most and never world-readable; the explicit
        # mode cannot be widened and the clamp below re-asserts it.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        try:
            # Fail before writing if the temp file cannot be secured.
            _harden_file(temporary)
            with open(source, "rb") as src, os.fdopen(fd, "wb") as dst:
                fd = None  # ownership transferred to the file object
                for chunk in iter(lambda: src.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
        finally:
            if fd is not None:
                os.close(fd)
        if _sha256(temporary) != digest.digest():
            raise CommandError(
                f"Checksum mismatch copying {source} -> {destination}; private copy discarded."
            )
        if destination.exists():
            # A concurrent writer created the destination first: only accept it
            # when it is byte-identical, otherwise it is a conflicting file.
            if _sha256(destination) != digest.digest():
                raise CommandError(
                    f"Conflicting destination {destination}: bytes differ from {source}."
                )
            _harden_file(destination)
        else:
            os.replace(temporary, destination)
            _harden_file(destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return digest.hexdigest()


class Command(BaseCommand):
    help = (
        "Copy sensitive uploads from the public settings.MEDIA_ROOT into "
        "settings.PRIVATE_MEDIA_ROOT, verify byte equality, then delete the public "
        "source. Referenced fields (Transaction.payment_proof, "
        "Datawajahnew.image_user, Logsmartaccess2.image) are resolved from the "
        "database; every remaining regular file under MEDIA_ROOT/payment_proofs/, "
        "MEDIA_ROOT/imagetraining/, MEDIA_ROOT/tracking/ and the legacy "
        "MEDIA_ROOT/qris_images/ is migrated as an orphan with its relative path "
        "preserved, including files whose row/field was already removed. Run this "
        "AFTER `manage.py migrate` so the schema already points every field at its "
        "private storage; the command refuses to run while a target field still "
        "writes to MEDIA_ROOT. Preflight is all-or-nothing and use --dry-run first "
        "to report referenced/orphaned counts without touching files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            help=(
                "Validate every referenced and orphaned source and report counts "
                "without copying or deleting any file."
            ),
        )

    def handle(self, *args, **options):
        media_root = Path(settings.MEDIA_ROOT).resolve()
        if not media_root.is_dir():
            raise CommandError(f"MEDIA_ROOT {media_root} does not exist; nothing can be migrated.")
        private_root = Path(settings.PRIVATE_MEDIA_ROOT).resolve()
        if private_root == media_root or private_root in media_root.parents or media_root in private_root.parents:
            raise CommandError(
                f"PRIVATE_MEDIA_ROOT {private_root} and MEDIA_ROOT {media_root} must be "
                "distinct, non-nested directories; refusing to migrate."
            )

        referenced, referenced_uploads = self._collect_uploads(media_root)
        referenced_names = {upload.name for upload in referenced_uploads}
        orphans, orphan_counts = self._collect_orphans(media_root, private_root, referenced_names)

        uploads = referenced_uploads + orphans
        to_copy, already, errors = self._classify(uploads)

        for upload, reason in errors:
            self.stderr.write(self.style.ERROR(f"{upload.label}: {reason}"))

        if errors:
            raise CommandError(
                f"Aborting: {len(errors)} of {len(uploads)} file(s) failed validation "
                f"({referenced} referenced row(s), {len(orphans)} orphaned file(s)); "
                "no files were copied or deleted."
            )

        if options["dry_run"]:
            self._report_dry_run(
                referenced, referenced_uploads, orphans, orphan_counts, to_copy, already
            )
            return

        copied = 0
        removed = 0
        for upload in to_copy:
            try:
                digest = _copy_verified(upload.source, upload.destination)
                _harden_dir_chain(upload.destination.parent, private_root)
            except OSError as exc:
                raise CommandError(
                    f"Could not secure private copy {upload.destination} to 0640/0750: {exc}; "
                    f"public source {upload.source} left intact."
                ) from exc
            self._remove_source(upload)
            copied += 1
            removed += 1
            self.stdout.write(
                f"copied {upload.kind} {upload.name} (sha256 {digest[:12]}) into private storage"
            )
        for upload in already:
            # Tighten the existing private copy and its directory chain before
            # the public source is deleted, so the only remaining copy is
            # confidential.  If hardening fails the source is left intact.
            try:
                _harden_file(upload.destination)
                _harden_dir_chain(upload.destination.parent, private_root)
            except OSError as exc:
                raise CommandError(
                    f"Could not secure private copy {upload.destination} to 0640/0750: {exc}; "
                    f"public source {upload.source} left intact."
                ) from exc
            self._remove_source(upload)
            removed += 1
            self.stdout.write(
                f"verified existing private copy; deleted public source {upload.name}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Migration complete: {copied} copied, {len(already)} already present, "
                f"{removed} public source(s) removed "
                f"({referenced} referenced row(s), {len(referenced_uploads)} referenced file(s), "
                f"{len(orphans)} orphaned file(s))."
            )
        )

    def _report_dry_run(self, referenced, referenced_uploads, orphans, orphan_counts, to_copy, already):
        ref_copy = sum(1 for upload in to_copy if upload.kind == REFERENCED)
        orphan_copy = len(to_copy) - ref_copy
        ref_already = sum(1 for upload in already if upload.kind == REFERENCED)
        orphan_already = len(already) - ref_already
        total = len(referenced_uploads) + len(orphans)
        self.stdout.write(f"Referenced rows scanned: {referenced}")
        self.stdout.write(f"Distinct referenced files: {len(referenced_uploads)}")
        self.stdout.write(f"Orphaned files discovered: {len(orphans)}")
        for root_name in ORPHAN_SWEEP_ROOTS:
            self.stdout.write(f"  {root_name}/: {orphan_counts.get(root_name, 0)}")
        self.stdout.write(f"Total distinct files: {total}")
        self.stdout.write(
            f"Would copy into private storage: {len(to_copy)} "
            f"(referenced {ref_copy}, orphaned {orphan_copy})"
        )
        self.stdout.write(
            f"Already byte-identical in private storage: {len(already)} "
            f"(referenced {ref_already}, orphaned {orphan_already})"
        )
        self.stdout.write(
            f"Public sources that would be deleted: {len(to_copy) + len(already)} "
            f"(referenced {ref_copy + ref_already}, orphaned {orphan_copy + orphan_already})"
        )
        self.stdout.write(self.style.WARNING("Dry run: no files were copied or deleted."))

    def _collect_uploads(self, media_root):
        """Return (referenced row count, distinct referenced Upload objects)."""
        uploads = {}
        referenced = 0
        for app_label, model_name, field_name in TARGETS:
            model = apps.get_model(app_label, model_name)
            field = model._meta.get_field(field_name)
            label = f"{model._meta.label}.{field_name}"
            if not isinstance(field, models.FileField):
                raise CommandError(f"{label} is not a file field; refusing to migrate.")

            storage = field.storage
            if not isinstance(storage, FileSystemStorage):
                raise CommandError(
                    f"{label} uses {type(storage).__name__}, which is not a local filesystem "
                    "storage; refusing to migrate."
                )
            destination_root = Path(storage.location).resolve()
            if destination_root == media_root:
                raise CommandError(
                    f"{label} still writes to the public MEDIA_ROOT; run `manage.py migrate` "
                    "before this command."
                )

            queryset = (
                model._default_manager.filter(**{f"{field_name}__isnull": False})
                .exclude(**{field_name: ""})
                .order_by("pk")
            )
            for pk, name in queryset.values_list("pk", field_name).iterator():
                referenced += 1
                if not name or name in uploads:
                    continue
                uploads[name] = Upload(
                    label=f"{label}#{pk}",
                    name=name,
                    source=self._within(media_root, name, label, "MEDIA_ROOT"),
                    destination=self._within(destination_root, name, label, "private storage"),
                )
        return referenced, list(uploads.values())

    def _collect_orphans(self, media_root, private_root, referenced_names):
        """Return (orphaned Upload objects, per-root counts) for the sweep roots."""
        orphans = []
        counts = {}
        seen = set()
        for root_name in ORPHAN_SWEEP_ROOTS:
            root = media_root / root_name
            if root.is_symlink():
                raise CommandError(
                    f"Sweep root {root} is a symlink; refusing to traverse symlinks under "
                    "MEDIA_ROOT."
                )
            if not root.exists():
                counts[root_name] = 0
                continue
            if not root.is_dir():
                raise CommandError(
                    f"Sweep root {root} is not a directory; refusing to migrate."
                )

            found = 0
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                dirnames.sort()
                for dirname in dirnames:
                    candidate = Path(dirpath) / dirname
                    if candidate.is_symlink():
                        raise CommandError(
                            f"Refusing to traverse symlinked directory {candidate} under "
                            "MEDIA_ROOT; migrate or remove it first."
                        )
                for filename in sorted(filenames):
                    path = Path(dirpath) / filename
                    if path.is_symlink():
                        raise CommandError(
                            f"Refusing to migrate symlinked file {path} under MEDIA_ROOT; "
                            "migrate or remove it first."
                        )
                    if not path.is_file():
                        raise CommandError(
                            f"{path} is not a regular file; refusing to migrate."
                        )
                    relative = path.relative_to(media_root).as_posix()
                    if relative in referenced_names or relative in seen:
                        continue
                    seen.add(relative)
                    orphans.append(
                        Upload(
                            label=f"orphan:{relative}",
                            name=relative,
                            source=self._within(media_root, relative, f"orphan {relative}", "MEDIA_ROOT"),
                            destination=self._within(
                                private_root, relative, f"orphan {relative}", "private storage"
                            ),
                            kind=ORPHANED,
                        )
                    )
                    found += 1
            counts[root_name] = found
        return orphans, counts

    @staticmethod
    def _within(root, name, label, where):
        """Resolve ``name`` under ``root``, refusing anything that escapes it."""
        if not name or os.path.isabs(name):
            raise CommandError(f"{label} stores an invalid file name {name!r}; refusing to migrate.")
        candidate = (root / name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise CommandError(
                f"{label} file name {name!r} escapes {where}; refusing to migrate."
            ) from exc
        return candidate

    def _classify(self, uploads):
        """Split uploads into copy/already-done work and validation errors."""
        to_copy = []
        already = []
        errors = []
        for upload in uploads:
            source = upload.source
            destination = upload.destination
            if not source.exists():
                detail = (
                    "a private destination exists but cannot be verified"
                    if destination.exists()
                    else "no private copy found"
                )
                errors.append(
                    (upload, f"missing source {source} ({detail}); byte equality cannot be verified")
                )
                continue
            if not source.is_file():
                errors.append((upload, f"source {source} is not a regular file"))
                continue
            if destination.exists():
                if not destination.is_file():
                    errors.append(
                        (upload, f"conflicting destination {destination} is not a regular file")
                    )
                    continue
                if (
                    source.stat().st_size != destination.stat().st_size
                    or _sha256(source) != _sha256(destination)
                ):
                    errors.append(
                        (upload, f"conflicting destination {destination}: bytes differ from {source}")
                    )
                    continue
                already.append(upload)
            else:
                to_copy.append(upload)
        return to_copy, already, errors

    def _remove_source(self, upload):
        """Delete the public source only once the private copy is verified."""
        try:
            upload.source.unlink()
        except OSError as exc:
            raise CommandError(
                f"Private copy verified, but the public source {upload.source} could not be "
                f"removed: {exc}"
            ) from exc
