"""Manage a project-local PostgreSQL cluster; never install a Windows service or load financial data."""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

import psycopg
from psycopg import sql

from .db import ROOT, configuration


def pg_command(binary, arguments, *, check=True):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    # pg_ctl starts a detached server: inherited PIPE handles can keep communicate()
    # waiting after pg_ctl exits on Windows. Server diagnostics use its -l log file.
    result = subprocess.run([str(binary), *map(str, arguments)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags, timeout=60)
    if check and result.returncode:
        raise ValueError(f'{binary.name} failed (exit {result.returncode}); inspect the local server log')
    return result.returncode


def admin_configuration(root=ROOT):
    data = json.loads((Path(root)/'.local/postgres/admin.json').read_text(encoding='utf-8'))
    return dict(host='127.0.0.1', port=data['port'], dbname='postgres',
                user='finlyst_admin', password=data['password'], connect_timeout=5)


def initialize(root, binary_dir, port):
    local = root/'.local/postgres'
    data = local/'data'
    admin_path = local/'admin.json'
    environment = root/'.env.db'
    if (data/'PG_VERSION').exists():
        if not admin_path.exists() or not environment.exists():
            raise ValueError('Existing cluster configuration is incomplete; restore its private configuration before continuing')
    else:
        if environment.exists():
            raise ValueError('Existing .env.db without this cluster: refusing to replace database configuration')
        local.mkdir(parents=True, exist_ok=True)
        if not admin_path.exists():
            admin_path.write_text(json.dumps(dict(port=port, password=secrets.token_urlsafe(36))), encoding='utf-8')
        admin = admin_configuration(root)
        if admin['port'] != port:
            raise ValueError('Port differs from saved cluster configuration')
        password_file = local/'init_password.tmp'
        try:
            password_file.write_text(admin['password']+'\n', encoding='utf-8')
            pg_command(binary_dir/'initdb.exe' if os.name == 'nt' else binary_dir/'initdb',
                       ['-D', data, '-U', 'finlyst_admin', '--pwfile', password_file,
                        '--auth=scram-sha-256', '--encoding=UTF8', '--locale=C'])
        finally:
            password_file.unlink(missing_ok=True)
        with (data/'postgresql.conf').open('a', encoding='utf-8') as stream:
            stream.write(f"\n# Finlyst local-only instance\nlisten_addresses = '127.0.0.1'\nport = {port}\n"
                         "password_encryption = 'scram-sha-256'\nlog_statement = 'none'\n")
        environment.write_text('FINLYST_DB_HOST=127.0.0.1\n'+f'FINLYST_DB_PORT={port}\n'
            'FINLYST_DB_NAME=finlyst\nFINLYST_DB_USER=finlyst_owner\n'+
            f'FINLYST_DB_PASSWORD={secrets.token_urlsafe(36)}\nFINLYST_DB_SSLMODE=disable\n', encoding='utf-8')
    control(root, binary_dir, 'start')
    settings = configuration(root)
    expected = admin_configuration(root)
    if (settings['host'], settings['port'], settings['dbname'], settings['user']) != (
            '127.0.0.1', expected['port'], 'finlyst', 'finlyst_owner'):
        raise ValueError('Local setup requires its own saved localhost database configuration')
    with psycopg.connect(**expected, autocommit=True) as connection:
        if not connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (settings['user'],)).fetchone():
            connection.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(
                sql.Identifier(settings['user']), sql.Literal(settings['password'])))
        if not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', (settings['dbname'],)).fetchone():
            connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(
                sql.Identifier(settings['dbname']), sql.Identifier(settings['user'])))
    # Confirm the saved owner credential works; never reset an existing role password implicitly.
    with psycopg.connect(**settings):
        pass


def control(root, binary_dir, command):
    local = root/'.local/postgres'
    data = local/'data'
    binary = binary_dir/('pg_ctl.exe' if os.name == 'nt' else 'pg_ctl')
    if not (data/'PG_VERSION').exists():
        raise ValueError('Local cluster is not initialized; run init first')
    running = pg_command(binary, ['-D', data, 'status'], check=False) == 0
    if command == 'status':
        print('Local PostgreSQL is ' + ('running.' if running else 'stopped.'))
    elif command == 'start' and not running:
        pg_command(binary, ['-D', data, '-l', local/'server.log', '-w', '-t', '30', 'start'])
    elif command == 'stop' and running:
        pg_command(binary, ['-D', data, '-m', 'fast', '-w', '-t', '30', 'stop'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'start', 'stop', 'status'))
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--bin-dir', type=Path, help='directory containing initdb and pg_ctl')
    parser.add_argument('--port', type=int, default=55432)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    binary_dir = args.bin_dir or root/'.local/postgres/runtime/pgsql/bin'
    try:
        if not 1 <= args.port <= 65535:
            raise ValueError('Port must be between 1 and 65535')
        if args.command == 'init':
            initialize(root, binary_dir, args.port)
            print('Local PostgreSQL initialized; private settings saved in .env.db. No financial data imported.')
        else:
            control(root, binary_dir, args.command)
        return 0
    except ValueError as exc:
        print(f'Local PostgreSQL setup: {exc}', file=sys.stderr)
    except (OSError, subprocess.SubprocessError, psycopg.Error) as exc:
        print(f'Local PostgreSQL setup failed ({type(exc).__name__}); inspect .local/postgres/server.log', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
