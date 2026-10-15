"""Regression tests for the marketplace product-media migration path.

History: ``marketplace/0003_product_image.py`` originally ran raw
``ALTER TABLE marketplace_product DROP COLUMN IF EXISTS image CASCADE`` (and the
same for ``created_at``/``updated_at``) as *database* operations while declaring
the fields only in migration *state*. Because Django's ``SeparateDatabaseAndState``
runs the database operations verbatim, every database upgrading through that node
had its populated ``image`` filename (and any pre-existing timestamps) physically
dropped; ``0008_fix_product_media_fields`` then re-added the columns empty, so the
data was silently lost.

Each test builds a throwaway PostgreSQL database, migrates it to the historical
``0002_product_image`` state, inserts a real row (image filename / legacy
timestamps), then migrates forward to every graph leaf and asserts the data
survives.

Why a scratch database on the ``default`` alias (and not a second alias):

* The shared test database cannot be rewound to ``0002_product_image`` because
  several later nodes contain ``RunSQL`` operations without ``reverse_sql`` and
  therefore cannot be unapplied.
* Historical data migrations (e.g. ``0010_product_seller_and_transaction_seller``)
  query ``Model.objects`` without ``.using(schema_editor.connection.alias)``, and
  ``MigrationRecorder`` records against ``connection.alias``. Both resolve to the
  ``default`` alias, so running the graph on a *different* alias would silently
  migrate the real ``default`` database instead of the scratch one. The
  ``default`` alias is therefore temporarily repointed at the scratch database
  and restored (with the scratch database dropped) in cleanup.
"""

import uuid

from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import ConnectionHandler, OperationalError
from django.test import TransactionTestCase

BASE_NODE = ('marketplace', '0002_product_image')
SCRATCH_DB_PREFIX = 'test_mig_media_'


class ProductMediaMigrationTests(TransactionTestCase):
    """Product media must survive an upgrade through the 0003 node."""

    def setUp(self):
        super().setUp()
        default = connections['default']
        self._orig_settings = dict(default.settings_dict)
        self._db_name = f'{SCRATCH_DB_PREFIX}{uuid.uuid4().hex[:12]}'
        self._db_created = False
        self._default_rebound = False

        # A private settings dict so repointing ``default`` cannot follow the
        # maintenance connection; used only for raw CREATE/DROP DATABASE.
        self._maintenance = ConnectionHandler(
            {'default': dict(self._orig_settings)}
        )['default']

        # Cleanup is registered before anything that can fail, so a skipTest or
        # an error still restores the default alias and drops the scratch DB.
        self.addCleanup(self._cleanup)

        try:
            with self._maintenance.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE {self._quote(self._db_name)}')
        except OperationalError as exc:  # pragma: no cover - env dependent
            self.skipTest(f'cannot create scratch database: {exc}')
        self._db_created = True

        # Repoint the default alias at the scratch database so the whole graph
        # (including historical RunPython data migrations) runs against it.
        default.close()
        default.settings_dict['NAME'] = self._db_name
        self._default_rebound = True

    def _cleanup(self):
        default = connections['default']
        if self._default_rebound:
            default.close()
            default.settings_dict.clear()
            default.settings_dict.update(self._orig_settings)
            default.close()
            self._default_rebound = False
        if self._db_created:
            try:
                with self._maintenance.cursor() as cursor:
                    cursor.execute(
                        'SELECT pg_terminate_backend(pid) FROM pg_stat_activity '
                        'WHERE datname = %s AND pid <> pg_backend_pid()',
                        [self._db_name],
                    )
                    cursor.execute(
                        f'DROP DATABASE IF EXISTS {self._quote(self._db_name)}'
                    )
            finally:
                self._db_created = False
        self._maintenance.close()

    # -- helpers ---------------------------------------------------------

    def _quote(self, identifier):
        return self._maintenance.ops.quote_name(identifier)

    def _migrate(self, targets):
        """Run the real graph forward; a bare target tuple is normalised."""
        if isinstance(targets, tuple):
            targets = [targets]
        executor = MigrationExecutor(connections['default'])
        executor.migrate(list(targets))

    def _leaf_nodes(self):
        return MigrationExecutor(connections['default']).loader.graph.leaf_nodes()

    def _seed_product(self, image_name, created_at=None, updated_at=None):
        """Insert a user/store/product row at the ``0002_product_image`` state.

        Raw SQL is used because the historical model state has neither the
        ``seller`` column (added in ``0010``) nor the newer user name columns.
        """
        with connections['default'].cursor() as cursor:
            cursor.execute(
                'INSERT INTO users_user '
                '(password, is_superuser, username, is_staff, is_active, '
                ' date_joined, email, role) '
                "VALUES ('x', false, %s, false, true, NOW(), %s, 'SELLER') "
                'RETURNING id',
                ['legacy_seller', 'legacy-seller@example.com'],
            )
            user_id = cursor.fetchone()[0]

            cursor.execute(
                'INSERT INTO marketplace_store (owner_id, name, description) '
                'VALUES (%s, %s, %s) RETURNING id',
                [user_id, 'Legacy Store', ''],
            )
            store_id = cursor.fetchone()[0]

            columns = ['name', 'price', 'stock', 'description', 'store_id', 'image']
            values = ['Legacy Product', 10, 5, 'legacy', store_id, image_name]
            placeholders = ['%s'] * len(columns)
            for name, value in (('created_at', created_at), ('updated_at', updated_at)):
                if value is not None:
                    columns.append(name)
                    values.append(value)
                    placeholders.append('%s')
            cursor.execute(
                'INSERT INTO marketplace_product ({}) VALUES ({}) RETURNING id'.format(
                    ', '.join(columns), ', '.join(placeholders)
                ),
                values,
            )
            return cursor.fetchone()[0]

    def _product_columns(self):
        with connections['default'].cursor() as cursor:
            cursor.execute(
                'SELECT column_name FROM information_schema.columns '
                "WHERE table_name = 'marketplace_product'"
            )
            return {row[0] for row in cursor.fetchall()}

    def _fetch_image(self, product_id):
        with connections['default'].cursor() as cursor:
            cursor.execute(
                'SELECT image FROM marketplace_product WHERE id = %s',
                [product_id],
            )
            return cursor.fetchone()[0]

    # -- tests -----------------------------------------------------------

    def test_image_filename_survives_upgrade_through_0003(self):
        """A populated Product.image filename must not be dropped on upgrade."""
        self._migrate(BASE_NODE)
        product_id = self._seed_product('product_images/legacy-photo.jpg')

        self.assertIn('image', self._product_columns())
        self.assertEqual(self._fetch_image(product_id), 'product_images/legacy-photo.jpg')

        self._migrate(self._leaf_nodes())

        self.assertIn('image', self._product_columns())
        self.assertEqual(self._fetch_image(product_id), 'product_images/legacy-photo.jpg')

    def test_legacy_timestamps_survive_upgrade_through_0003(self):
        """Pre-existing created_at/updated_at values must not be dropped."""
        self._migrate(BASE_NODE)
        # Simulate an older deployed schema that already carried timestamps.
        with connections['default'].cursor() as cursor:
            cursor.execute(
                'ALTER TABLE marketplace_product '
                'ADD COLUMN created_at timestamp with time zone, '
                'ADD COLUMN updated_at timestamp with time zone'
            )
        product_id = self._seed_product(
            'product_images/legacy.jpg',
            created_at='2020-01-01 00:00:00+00',
            updated_at='2021-06-15 12:00:00+00',
        )

        self._migrate(self._leaf_nodes())

        with connections['default'].cursor() as cursor:
            cursor.execute(
                'SELECT image, created_at, updated_at '
                'FROM marketplace_product WHERE id = %s',
                [product_id],
            )
            image, created_at, updated_at = cursor.fetchone()
        self.assertEqual(image, 'product_images/legacy.jpg')
        self.assertIsNotNone(created_at)
        self.assertEqual(created_at.year, 2020)
        self.assertIsNotNone(updated_at)
        self.assertEqual(updated_at.year, 2021)
