"""Focused, isolated smoke checks for private-media on-disk confidentiality.

These run without a database (``SimpleTestCase``) and use temporary directories,
so they are safe to run on their own::

    python manage.py test apps.marketplace.tests.test_private_media_permissions

They assert the mode contract only (0640 files / 0750 directories, never
world-readable, never widened), that runtime saves clamp pre-existing
directories and refuse to escape the private root through a symlinked
destination, and that the migration command preserves the public source when a
permission hardening step fails mid-copy.  No test mutates the process umask
(the ``0o000`` probe runs in a throwaway subprocess) and no test inspects
source text.
"""

import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from unittest import mock, skipUnless

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import SimpleTestCase

from smartlocker.storage import (
    PRIVATE_DIRECTORY_MODE,
    PRIVATE_FILE_MODE,
    private_media_storage,
    secure_private_directory,
    secure_private_file,
)

from apps.marketplace.management.commands import migrate_private_uploads as migration


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class PrivateMediaStorageModeTests(SimpleTestCase):
    """Runtime uploads land as 0640 files inside 0750 directories."""

    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp(prefix='private-media-mode-'))
        self.addCleanup(shutil.rmtree, self.root, True)

        # ``private_media_storage`` binds its location at import time, so point
        # it at the temporary directory and restore afterwards.
        self._original_location = private_media_storage._location
        private_media_storage._location = str(self.root)
        private_media_storage.__dict__.pop('base_location', None)
        private_media_storage.__dict__.pop('location', None)

        def _restore():
            private_media_storage._location = self._original_location
            private_media_storage.__dict__.pop('base_location', None)
            private_media_storage.__dict__.pop('location', None)

        self.addCleanup(_restore)

    def test_configured_modes_are_private(self):
        self.assertEqual(private_media_storage.file_permissions_mode, PRIVATE_FILE_MODE)
        self.assertEqual(
            private_media_storage.directory_permissions_mode, PRIVATE_DIRECTORY_MODE
        )

    def test_saved_file_and_directories_are_never_world_readable(self):
        name = private_media_storage.save(
            'nested/deep/secret.bin', ContentFile(b'private-bytes')
        )
        path = Path(private_media_storage.path(name))
        self.assertEqual(_mode(path), PRIVATE_FILE_MODE)
        self.assertEqual(_mode(path.parent), PRIVATE_DIRECTORY_MODE)
        self.assertEqual(_mode(path.parent.parent), PRIVATE_DIRECTORY_MODE)
        self.assertEqual(_mode(path) & 0o007, 0)

    def test_existing_permissive_nested_dirs_are_clamped(self):
        """A pre-existing 0755 nested destination is tightened on save."""
        nested = self.root / 'a' / 'b'
        nested.mkdir(parents=True)
        os.chmod(self.root, 0o755)
        os.chmod(self.root / 'a', 0o755)
        os.chmod(nested, 0o755)

        name = private_media_storage.save('a/b/secret.bin', ContentFile(b'x'))
        path = Path(private_media_storage.path(name))

        self.assertEqual(_mode(path), PRIVATE_FILE_MODE)
        self.assertEqual(_mode(path) & 0o007, 0)
        # every level from the file up to the storage root is 0750
        self.assertEqual(_mode(nested), PRIVATE_DIRECTORY_MODE)
        self.assertEqual(_mode(self.root / 'a'), PRIVATE_DIRECTORY_MODE)
        self.assertEqual(_mode(self.root), PRIVATE_DIRECTORY_MODE)

    def test_existing_permissive_nested_dirs_clamped_under_umask_000(self):
        """Same guarantee when the process umask is 0o000.

        The umask is a process-wide, thread-shared setting, so it is exercised
        only inside a throwaway subprocess; this test process never changes it.
        """
        script = textwrap.dedent(
            """
            import os, stat, tempfile
            from pathlib import Path

            os.umask(0o000)
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'smartlocker.settings')
            import django
            django.setup()

            from django.core.files.base import ContentFile
            from smartlocker.storage import (
                PRIVATE_DIRECTORY_MODE, PRIVATE_FILE_MODE, PrivateMediaStorage,
            )

            root = Path(tempfile.mkdtemp(prefix='umask-probe-'))
            (root / 'a' / 'b').mkdir(parents=True)
            os.chmod(root, 0o755)
            os.chmod(root / 'a', 0o755)
            os.chmod(root / 'a' / 'b', 0o755)

            storage = PrivateMediaStorage(location=str(root))
            name = storage.save('a/b/secret.bin', ContentFile(b'x'))
            path = Path(storage.path(name))

            mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)
            assert mode(path) == PRIVATE_FILE_MODE, oct(mode(path))
            assert mode(path) & 0o007 == 0, oct(mode(path))
            for directory in (root / 'a' / 'b', root / 'a', root):
                assert mode(directory) == PRIVATE_DIRECTORY_MODE, (directory, oct(mode(directory)))
            print('UMASK-PROBE-OK')
            """
        )
        result = subprocess.run(
            [sys.executable, '-c', script],
            cwd=str(settings.BASE_DIR),
            env={
                **os.environ.copy(),
                'MEDIA_ROOT': str(self.root / 'probe_media'),
                'PRIVATE_MEDIA_ROOT': str(self.root / 'probe_private'),
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0, msg=f'stdout={result.stdout!r} stderr={result.stderr!r}'
        )
        self.assertIn('UMASK-PROBE-OK', result.stdout)

    @skipUnless(sys.platform.startswith('linux'), 'requires Linux symlink semantics')
    def test_symlinked_destination_cannot_escape_private_root(self):
        """A nested symlink pointing outside the private root aborts the save.

        ``os.path.realpath`` collapses the link to an external directory, so a
        naive implementation would chmod that outside directory and create the
        file inside it.  The save must instead fail before touching anything
        outside the private root.
        """
        outside = Path(tempfile.mkdtemp(prefix='private-media-outside-'))
        self.addCleanup(shutil.rmtree, outside, True)
        os.chmod(outside, 0o777)

        nested = self.root / 'nested'
        nested.mkdir()
        escape = nested / 'escape'
        escape.symlink_to(outside, target_is_directory=True)
        self.assertTrue(escape.is_symlink())

        with self.assertRaises(OSError):
            private_media_storage.save(
                'nested/escape/secret.bin', ContentFile(b'private-bytes')
            )

        # The outside directory is untouched: same mode, no file created.
        self.assertEqual(_mode(outside), 0o777)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((outside / 'secret.bin').exists())
        # The link itself was not replaced by a real directory.
        self.assertTrue(escape.is_symlink())

    @skipUnless(sys.platform.startswith('linux'), 'requires Linux symlink semantics')
    def test_symlink_directly_under_root_cannot_escape(self):
        """A symlink directly under the root resolves outside and is rejected."""
        outside = Path(tempfile.mkdtemp(prefix='private-media-outside-'))
        self.addCleanup(shutil.rmtree, outside, True)
        os.chmod(outside, 0o777)

        escape = self.root / 'link'
        escape.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(OSError):
            private_media_storage.save('link/secret.bin', ContentFile(b'x'))

        # The outside directory keeps its permissive mode: the save refused to
        # touch it rather than clamping it to 0o750.
        self.assertEqual(_mode(outside), 0o777)
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_stricter_modes_are_not_widened(self):
        target = self.root / 'strict.bin'
        target.write_bytes(b'x')
        os.chmod(target, 0o600)
        secure_private_file(target)
        self.assertEqual(_mode(target), 0o600)

        directory = self.root / 'strictdir'
        directory.mkdir()
        os.chmod(directory, 0o700)
        secure_private_directory(directory)
        self.assertEqual(_mode(directory), 0o700)

    def test_permissive_modes_are_tightened(self):
        target = self.root / 'loose.bin'
        target.write_bytes(b'x')
        os.chmod(target, 0o644)
        secure_private_file(target)
        self.assertEqual(_mode(target), PRIVATE_FILE_MODE)

        directory = self.root / 'loosedir'
        directory.mkdir()
        os.chmod(directory, 0o755)
        secure_private_directory(directory)
        self.assertEqual(_mode(directory), PRIVATE_DIRECTORY_MODE)

    def test_chmod_failure_is_loud(self):
        target = self.root / 'loose.bin'
        target.write_bytes(b'x')
        os.chmod(target, 0o644)
        with mock.patch(
            'smartlocker.storage.os.chmod', side_effect=PermissionError('denied')
        ):
            with self.assertRaises(OSError):
                secure_private_file(target)


class MigrationCopyModeTests(SimpleTestCase):
    """The migration copy hardens temp/destination modes and never deletes the
    public source unless the private copy is secured."""

    def setUp(self):
        super().setUp()
        self.base = Path(tempfile.mkdtemp(prefix='migrate-mode-'))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.public = self.base / 'media'
        self.private = self.base / 'private_media'
        self.public.mkdir()

    def _source(self, payload=b'payload'):
        source = self.public / 'payment_proofs' / 'proof.bin'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
        return source

    def test_copy_hardens_temp_and_destination_and_keeps_source(self):
        source = self._source(b'secret-payload')
        destination = self.private / 'payment_proofs' / 'proof.bin'

        digest = migration._copy_verified(source, destination)

        self.assertEqual(digest, hashlib.sha256(b'secret-payload').hexdigest())
        self.assertEqual(destination.read_bytes(), b'secret-payload')
        self.assertTrue(source.exists())  # copying never deletes the source
        self.assertEqual(_mode(destination), PRIVATE_FILE_MODE)
        self.assertEqual(_mode(destination.parent), PRIVATE_DIRECTORY_MODE)
        self.assertEqual(_mode(destination) & 0o007, 0)
        # no temporary file is left behind
        self.assertEqual(list(destination.parent.glob('.*.part')), [])

    def test_existing_destination_is_tightened_and_bytes_preserved(self):
        source = self._source(b'identical')
        destination = self.private / 'payment_proofs' / 'proof.bin'
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.private, 0o755)
        os.chmod(destination.parent, 0o755)
        destination.write_bytes(b'identical')
        os.chmod(destination, 0o666)

        migration._copy_verified(source, destination)

        self.assertEqual(destination.read_bytes(), b'identical')
        self.assertEqual(_mode(destination), PRIVATE_FILE_MODE)
        self.assertEqual(_mode(destination.parent), PRIVATE_DIRECTORY_MODE)

    def test_copy_failure_leaves_source_intact(self):
        source = self._source(b'keep-me')
        destination = self.private / 'blocked' / 'proof.bin'
        # A regular file where a directory is needed makes directory creation
        # fail before any byte is copied.
        self.private.mkdir(parents=True, exist_ok=True)
        (self.private / 'blocked').write_bytes(b'not a directory')

        with self.assertRaises(OSError):
            migration._copy_verified(source, destination)

        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), b'keep-me')
        self.assertFalse(destination.exists())


class MigrationCommandOrderingTests(SimpleTestCase):
    """``Command.handle`` must copy -> secure -> delete in order: a real chmod
    failure mid-copy aborts with ``CommandError`` and leaves the public source
    bytes untouched, with no temp artifact."""

    def setUp(self):
        super().setUp()
        self.base = Path(tempfile.mkdtemp(prefix='migrate-handle-'))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.public = self.base / 'media'
        self.private = self.base / 'private_media'
        self.public.mkdir()

    def test_secure_failure_aborts_and_preserves_source(self):
        payload = b'exact-public-source-bytes'
        source = self.public / 'payment_proofs' / 'proof.bin'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
        destination = self.private / 'payment_proofs' / 'proof.bin'
        # A pre-existing, permissive destination directory forces a real chmod
        # during the copy's directory-hardening step.
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(destination.parent, 0o755)
        upload = migration.Upload(
            label='marketplace.Transaction.payment_proof#1',
            name='payment_proofs/proof.bin',
            source=source,
            destination=destination,
        )

        command = migration.Command()
        real_chmod = os.chmod

        def deny_directory_chmod(path, mode, *args, **kwargs):
            # Fail exactly while a directory is being secured, i.e. after the
            # destination path is resolved but before any byte is copied and
            # long before the public source could be deleted.
            if os.path.isdir(path):
                raise PermissionError('denied')
            return real_chmod(path, mode, *args, **kwargs)

        with self.settings(MEDIA_ROOT=str(self.public), PRIVATE_MEDIA_ROOT=str(self.private)):
            with mock.patch.object(command, '_collect_uploads', return_value=(1, [upload])):
                with mock.patch.object(command, '_collect_orphans', return_value=([], {})):
                    with mock.patch(
                        'smartlocker.storage.os.chmod', side_effect=deny_directory_chmod
                    ):
                        with self.assertRaises(migration.CommandError):
                            command.handle(dry_run=False)

        # Public source is byte-for-byte intact and still present.
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), payload)
        # No private copy and no temp artifact were left behind.
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.private.rglob('*.part')), [])
