import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction


TABLE = 'marketplace_product'
PRESERVATION_MIGRATION = ('marketplace', '0003_product_image')
PRESERVED_FIELDS = ('image', 'created_at', 'updated_at')


def _canonical_json(value):
    return json.dumps(
        value,
        cls=DjangoJSONEncoder,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')


def _timestamp(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class Command(BaseCommand):
    help = 'Preserve Product image and timestamps across the historical destructive migration.'

    def add_arguments(self, parser):
        operation = parser.add_mutually_exclusive_group(required=True)
        operation.add_argument('--export', metavar='ABSOLUTE_JSON_PATH')
        operation.add_argument('--restore', metavar='ABSOLUTE_JSON_PATH')

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('This preservation command requires PostgreSQL.')
        path = self._manifest_path(options['export'] or options['restore'])
        if options['export']:
            self._export(path)
        else:
            self._restore(path)

    def _manifest_path(self, value):
        path = Path(value)
        if not path.is_absolute():
            raise CommandError('The manifest path must be absolute.')
        path = path.resolve()
        base_dir = Path(settings.BASE_DIR).resolve()
        if path == base_dir or base_dir in path.parents:
            raise CommandError('Store the manifest outside the repository.')
        if not path.parent.is_dir():
            raise CommandError('The manifest parent directory must already exist.')
        return path

    def _table_names(self, cursor):
        return {table.name for table in connection.introspection.get_table_list(cursor)}

    def _columns(self, cursor):
        return {
            column.name: column
            for column in connection.introspection.get_table_description(cursor, TABLE)
        }

    def _applied_migrations(self, cursor, tables):
        if 'django_migrations' not in tables:
            raise CommandError('django_migrations is missing; migration history cannot be verified.')
        cursor.execute('SELECT app, name FROM django_migrations ORDER BY app, name')
        return {(app, name) for app, name in cursor.fetchall()}

    def _export(self, path):
        with connection.cursor() as cursor:
            tables = self._table_names(cursor)
            if TABLE not in tables:
                raise CommandError(f'{TABLE} does not exist in this database.')
            applied = self._applied_migrations(cursor, tables)
            if PRESERVATION_MIGRATION in applied:
                raise CommandError(
                    'marketplace.0003_product_image is already applied; restore these values only from a trusted backup.'
                )
            columns = self._columns(cursor)
            if 'id' not in columns:
                raise CommandError(f'{TABLE}.id is missing; product rows cannot be keyed safely.')
            preserved = [field for field in PRESERVED_FIELDS if field in columns]
            quote = connection.ops.quote_name
            select_fields = ', '.join(quote(field) for field in ('id', *preserved))
            cursor.execute(f'SELECT {select_fields} FROM {quote(TABLE)} ORDER BY {quote("id")}')
            records = []
            for row in cursor.fetchall():
                values = {}
                for field, value in zip(preserved, row[1:]):
                    values[field] = value.isoformat() if isinstance(value, datetime) else value
                records.append({'id': row[0], 'values': values})

            schema = {
                field: {
                    'type': str(columns[field].type_code),
                    'nullable': bool(columns[field].null_ok),
                }
                for field in preserved
            }
            payload = {
                'format_version': 1,
                'database_name': str(connection.settings_dict['NAME']),
                'exported_at': datetime.now(timezone.utc).isoformat(),
                'applied_migrations': [
                    {'app': app, 'name': name} for app, name in sorted(applied)
                ],
                'schema': schema,
                'columns': preserved,
                'products': records,
            }

        envelope = {
            'payload': payload,
            'sha256': hashlib.sha256(_canonical_json(payload)).hexdigest(),
        }
        descriptor, temporary_name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(envelope, stream, cls=DjangoJSONEncoder, sort_keys=True, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary_name, path)
            os.unlink(temporary_name)
        except FileExistsError as exc:
            raise CommandError('The manifest already exists; refusing to overwrite it.') from exc
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        self.stdout.write(self.style.SUCCESS(f'Exported {len(payload["products"])} Product rows to {path}.'))

    def _read_manifest(self, path):
        try:
            with path.open(encoding='utf-8') as stream:
                envelope = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CommandError('The manifest cannot be read as valid JSON.') from exc
        if not isinstance(envelope, dict) or not isinstance(envelope.get('payload'), dict):
            raise CommandError('The manifest envelope is invalid.')
        payload = envelope['payload']
        expected = envelope.get('sha256')
        actual = hashlib.sha256(_canonical_json(payload)).hexdigest()
        if not isinstance(expected, str) or not hmac.compare_digest(expected, actual):
            raise CommandError('The manifest checksum does not match.')
        if payload.get('format_version') != 1:
            raise CommandError('Unsupported manifest format version.')
        return payload

    def _restore(self, path):
        payload = self._read_manifest(path)
        if payload.get('database_name') != str(connection.settings_dict['NAME']):
            raise CommandError('The manifest belongs to a different database.')
        columns = payload.get('columns')
        records = payload.get('products')
        schema = payload.get('schema')
        applied_before = payload.get('applied_migrations')
        if (
            not isinstance(columns, list)
            or any(not isinstance(field, str) or field not in PRESERVED_FIELDS for field in columns)
            or len(columns) != len(set(columns))
            or not isinstance(records, list)
            or not isinstance(schema, dict)
            or set(schema) != set(columns)
            or any(not isinstance(schema[field], dict) or not isinstance(schema[field].get('type'), str) for field in columns)
            or not isinstance(applied_before, list)
            or any(
                not isinstance(item, dict)
                or not isinstance(item.get('app'), str)
                or not isinstance(item.get('name'), str)
                for item in applied_before
            )
            or not isinstance(payload.get('exported_at'), str)
        ):
            raise CommandError('The manifest data shape is invalid.')

        with connection.cursor() as cursor:
            tables = self._table_names(cursor)
            if TABLE not in tables:
                raise CommandError(f'{TABLE} does not exist in this database.')
            applied_now = self._applied_migrations(cursor, tables)
            if PRESERVATION_MIGRATION not in applied_now:
                raise CommandError('Apply marketplace.0003_product_image before restoring the manifest.')
            expected_history = {
                (item.get('app'), item.get('name'))
                for item in applied_before
                if isinstance(item, dict)
            }
            if len(expected_history) != len(applied_before) or not expected_history.issubset(applied_now):
                raise CommandError('The database migration history does not contain the exported baseline.')

            current_columns = self._columns(cursor)
            missing_columns = set(columns) - set(current_columns)
            if 'id' not in current_columns or missing_columns:
                raise CommandError(f'Required Product columns are missing: {sorted(missing_columns)}.')
            schema_mismatches = [
                field for field in columns
                if schema[field]['type'] != str(current_columns[field].type_code)
            ]
            if schema_mismatches:
                raise CommandError(f'Product column types changed since export: {schema_mismatches}.')
            quote = connection.ops.quote_name
            select_fields = ', '.join(quote(field) for field in ('id', *columns))
            cursor.execute(f'SELECT {select_fields} FROM {quote(TABLE)} ORDER BY {quote("id")}')
            current_rows = {row[0]: dict(zip(columns, row[1:])) for row in cursor.fetchall()}

        source_rows = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get('values'), dict):
                raise CommandError('The Product row data is invalid.')
            product_id = record.get('id')
            if type(product_id) is not int or product_id in source_rows:
                raise CommandError('The manifest contains a duplicate or invalid Product ID.')
            if set(record['values']) != set(columns):
                raise CommandError(f'The manifest fields do not match Product {product_id}.')
            source_rows[product_id] = record['values']

        if set(current_rows) != set(source_rows):
            missing = sorted(set(source_rows) - set(current_rows))
            added = sorted(set(current_rows) - set(source_rows))
            raise CommandError(f'Product IDs changed after export; missing={missing}, added={added}.')

        exported_at = _timestamp(payload.get('exported_at'))
        conflicts = []
        for field in columns:
            timestamp_field = field in {'created_at', 'updated_at'}
            current_values = [row[field] for row in current_rows.values()]
            default_timestamps = False
            if timestamp_field and current_values:
                normalized_values = [_timestamp(value) for value in current_values if value is not None]
                default_timestamps = (
                    len(normalized_values) == len(current_values)
                    and len(set(normalized_values)) == 1
                    and normalized_values[0] >= exported_at
                )
            for product_id, source_values in source_rows.items():
                source_value = source_values[field]
                current_value = current_rows[product_id][field]
                if timestamp_field:
                    if source_value is not None:
                        source_value = _timestamp(source_value)
                    if current_value is not None:
                        current_value = _timestamp(current_value)
                if current_value == source_value or current_value is None or (timestamp_field and default_timestamps):
                    continue
                conflicts.append(f'{product_id}:{field}')
        if conflicts:
            raise CommandError(f'Nondefault Product values conflict with the manifest: {conflicts}.')

        quote = connection.ops.quote_name
        assignments = ', '.join(f'{quote(field)} = %s' for field in columns)
        with transaction.atomic():
            with connection.cursor() as cursor:
                for product_id, source_values in source_rows.items():
                    if not columns:
                        continue
                    values = [
                        _timestamp(source_values[field])
                        if field in {'created_at', 'updated_at'} and source_values[field] is not None
                        else source_values[field]
                        for field in columns
                    ]
                    cursor.execute(
                        f'UPDATE {quote(TABLE)} SET {assignments} WHERE {quote("id")} = %s',
                        [*values, product_id],
                    )
                    if cursor.rowcount != 1:
                        raise CommandError(f'Product {product_id} changed during restore.')
        self.stdout.write(self.style.SUCCESS(f'Restored preserved values for {len(source_rows)} Product rows.'))
