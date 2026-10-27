"""PostgreSQL configuration and transactional SQL migrations; no financial importer."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from dotenv import dotenv_values
import psycopg
from psycopg.pq import TransactionStatus

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / 'migrations'
MIGRATION_LOCK = 741139310001


def configuration(root=ROOT, environ=None):
    """Explicit FINLYST_DB_* values override the ignored project .env.db file."""
    values = dict(dotenv_values(Path(root) / '.env.db', interpolate=False))
    values.update(os.environ if environ is None else environ)
    required = ('NAME', 'USER', 'PASSWORD')
    missing = [f'FINLYST_DB_{key}' for key in required if not values.get(f'FINLYST_DB_{key}')]
    if missing:
        raise ValueError('Missing database configuration: ' + ', '.join(missing))
    try:
        port = int(values.get('FINLYST_DB_PORT', '55432'))
        timeout = int(values.get('FINLYST_DB_CONNECT_TIMEOUT', '5'))
        if not 1 <= port <= 65535 or not 1 <= timeout <= 60:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('FINLYST_DB_PORT must be 1-65535; CONNECT_TIMEOUT must be 1-60 seconds') from None
    sslmode = values.get('FINLYST_DB_SSLMODE', 'prefer')
    if sslmode not in ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'):
        raise ValueError('Invalid FINLYST_DB_SSLMODE')
    return dict(host=values.get('FINLYST_DB_HOST', '127.0.0.1'), port=port,
                dbname=values['FINLYST_DB_NAME'], user=values['FINLYST_DB_USER'],
                password=values['FINLYST_DB_PASSWORD'], sslmode=sslmode, connect_timeout=timeout)


def connect(root=ROOT, *, environ=None, autocommit=False):
    config = configuration(root, environ)
    try:
        return psycopg.connect(**config, autocommit=autocommit, application_name='finlyst',
            options='-c search_path=pg_catalog,public -c statement_timeout=30000 -c lock_timeout=5000')
    except psycopg.Error as exc:
        raise ValueError(f'PostgreSQL connection failed ({type(exc).__name__}); '
                         'check server availability and FINLYST_DB_* settings') from None


def migration_files(directory=MIGRATIONS):
    """Checksums normalize CRLF/LF only, so Git checkout platform cannot alter history."""
    files = []
    for path in sorted(Path(directory).glob('*.sql')):
        match = re.fullmatch(r'(\d{3})_([a-z0-9_]+)\.sql', path.name)
        if not match:
            raise ValueError(f'Invalid migration filename: {path.name}')
        script = path.read_text(encoding='utf-8')
        if not script.strip():
            raise ValueError(f'Empty migration: {path.name}')
        files.append(dict(version=int(match[1]), name=path.name,
            checksum=hashlib.sha256(script.encode('utf-8')).hexdigest(), sql=script))
    if not files or [f['version'] for f in files] != list(range(1, len(files) + 1)):
        raise ValueError('Migrations must be a nonempty contiguous sequence starting at 001')
    return files


def _history(connection):
    if connection.execute("SELECT to_regclass('public.finlyst_schema_migrations')").fetchone()[0] is None:
        return []
    return connection.execute('SELECT version, name, checksum FROM public.finlyst_schema_migrations ORDER BY version').fetchall()


def _check_history(files, history):
    expected = [(f['version'], f['name'], f['checksum']) for f in files]
    if history != expected[:len(history)] or len(history) > len(expected):
        raise ValueError('Migration history/checksum differs from version-controlled files; restore the applied files')


def status(connection, migrations_dir=MIGRATIONS):
    files = migration_files(migrations_dir)
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Migration status requires an idle connection')
    with connection.transaction():
        history = _history(connection)
        _check_history(files, history)
    return dict(applied=[row[0] for row in history], pending=[f['version'] for f in files[len(history):]])


def migrate(connection, migrations_dir=MIGRATIONS):
    """Apply all pending trusted SQL files atomically under a database-scoped lock."""
    files = migration_files(migrations_dir)
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Migrations require an idle connection; commit/rollback caller work first')
    applied = []
    with connection.transaction():
        connection.execute('SELECT pg_advisory_xact_lock(%s)', (MIGRATION_LOCK,))
        connection.execute('''CREATE TABLE IF NOT EXISTS public.finlyst_schema_migrations (
            version integer PRIMARY KEY CHECK (version > 0), name text NOT NULL UNIQUE,
            checksum text NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
            applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        history = _history(connection)
        _check_history(files, history)
        for file in files[len(history):]:
            connection.execute(file['sql'], prepare=False)
            connection.execute('INSERT INTO public.finlyst_schema_migrations (version,name,checksum) VALUES (%s,%s,%s)',
                               (file['version'], file['name'], file['checksum']))
            applied.append(file['version'])
    return applied


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'status', 'migrate'))
    parser.add_argument('--root', type=Path, default=ROOT, help='directory containing ignored .env.db')
    args = parser.parse_args(argv)
    try:
        with connect(args.root) as connection:
            if args.command == 'check':
                row = connection.execute('SELECT current_database(), current_setting(\'server_version\')').fetchone()
                result = dict(database=row[0], server_version=row[1], connected=True)
            elif args.command == 'status':
                result = status(connection)
            else:
                result = dict(applied=migrate(connection))
        print(json.dumps(result, indent=2))
        return 0
    except ValueError as exc:
        print(f'Database error: {exc}', file=sys.stderr)
    except psycopg.Error as exc:
        # Never print SQL values, connection strings, credentials or private row contents.
        print(f'Database operation failed: {type(exc).__name__} (SQLSTATE {exc.sqlstate or "unavailable"})', file=sys.stderr)
    except OSError as exc:
        print(f'Database configuration/migration file error: {type(exc).__name__}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
