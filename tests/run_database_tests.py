"""Run the complete suite against a newly created, disposable local test database."""

import os
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg import sql

from src.db import ROOT, configuration
from src.local_postgres import admin_configuration


def main():
    name = 'finlyst_test_' + uuid4().hex
    owner = configuration()['user']
    with psycopg.connect(**admin_configuration(), autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name), sql.Identifier(owner)))
        try:
            env = dict(os.environ, FINLYST_TEST_DB_NAME=name)
            result = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
                                    cwd=ROOT, env=env)
            return result.returncode
        finally:
            # Only the exact freshly generated database can be dropped; never the application DB.
            if not name.startswith('finlyst_test_'):
                raise ValueError('Unsafe test database name')
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


if __name__ == '__main__':
    raise SystemExit(main())
