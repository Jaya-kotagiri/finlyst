"""Run Finlyst stages sequentially; reuse saved events unless API access is explicit."""

import argparse
from importlib import import_module
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
BANKS = ('kotak', 'sbi')
STAGES = ('bank', 'events', 'reconcile', 'relationships', 'classification',
          'audit', 'obligations', 'settlements', 'identities', 'integrity')


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--root', type=Path, default=ROOT, help='project directory (default: repository root)')
    commands = cli.add_subparsers(dest='command', required=True)
    for name in ('run', *STAGES, 'validate'):
        command = commands.add_parser(name, help='validate saved outputs without writing' if name == 'validate'
                                      else 'run the pipeline or a contiguous range' if name == 'run'
                                      else f'run the {name} stage')
        command.add_argument('--root', type=Path, default=argparse.SUPPRESS, help='project directory')
        if name == 'validate':
            command.add_argument('--all', required=True, action='store_true', help='check all stages and identity mappings')
            continue
        policy = command.add_mutually_exclusive_group()
        policy.add_argument('--offline', action='store_true', help='reuse saved events; never call the API (default)')
        if name in ('run', 'events'):
            policy.add_argument('--allow-api', action='store_true', help='allow generation of missing event outputs only')
        if name in ('bank', 'events', 'reconcile'):
            command.add_argument('--bank', choices=(*BANKS, 'all'), default='all')
        if name == 'run':
            command.add_argument('--from', dest='first', choices=STAGES, default=STAGES[0])
            command.add_argument('--to', dest='last', choices=STAGES, default=STAGES[-1])
    return cli


def read(path, schema):
    from .schema_validation import parse_document
    return parse_document(path.read_bytes(), schema, str(path))


def source_file(root, bank, *, notes=False):
    directory = root / 'input' / ('notes' if notes else 'bank') / bank
    pattern = '*.xlsx' if notes else '*.csv' if bank == 'kotak' else 'sbi_unencrypted.*'
    matches = sorted(p for p in directory.glob(pattern) if p.is_file() and not p.name.startswith('~$'))
    if len(matches) != 1:
        raise ValueError(f'{directory}: expected exactly one {pattern} source; found {len(matches)}')
    if not notes and bank == 'sbi' and matches[0].suffix.lower() != '.xlsx':
        raise ValueError(f'{directory}: SBI source must be the unencrypted .xlsx workbook')
    return matches[0]


def generate_missing_events(root, bank, source, destination):
    # The only API entry point. Never reached by help, validation or offline commands.
    import os
    from .events import generate_events, load_dotenv, OpenAI
    load_dotenv(root / '.env', override=False)
    if not os.environ.get('OPENAI_API_KEY'):
        raise ValueError('Set OPENAI_API_KEY in the environment or project .env for --allow-api')
    try:
        with OpenAI(api_key=os.environ['OPENAI_API_KEY'], max_retries=0, timeout=300) as client:
            generate_events(source, bank, destination, client)
    except Exception as exc:
        # API errors can contain private payloads or credentials; diagnostics stay local.
        raise ValueError(f'{bank}: event generation failed ({type(exc).__name__}); '
                         'no automatic retry; inspect retained event diagnostics if available') from None


def events_stage(root, banks, allow_api=False):
    from .notes import read_notes
    from .output_models import BankTransactions, EventDocument
    from .reconcile import validate_inputs
    from .validation import validate_transactions
    for bank in banks:
        destination = root / f'output/events/{bank}_events.json'
        if not destination.exists() and not allow_api:
            raise ValueError(f'{destination}: saved events required offline; '
                             'restore the validated output or explicitly use events --allow-api')
        source = source_file(root, bank, notes=True)
        transactions = read(root / f'output/bank/{bank}_transactions.json', BankTransactions)
        validate_transactions(transactions)
        if any(row['bank'] != bank.upper() for row in transactions):
            raise ValueError(f'{bank}: normalized bank input belongs to another bank')
        if not destination.exists():
            generate_missing_events(root, bank, source, destination)
        document = read(destination, EventDocument)
        if document['bank'] != bank.upper():
            raise ValueError(f'{destination}: bank does not match selected bank')
        validate_inputs(transactions, document)
        if read_notes(source) != document['source_rows']:
            raise ValueError(f'{destination}: saved events do not match current notes; review source changes')
        print(f'  {bank}: {len(document["events"])} saved events validated', flush=True)


def require_integrity(report):
    if report['result'] != 'PASS' or report['failed_checks']:
        raise ValueError('integrity checks failed: ' + ', '.join(report['failed_checks']))


def validate_all(root):
    """Replay existing checks in memory. No writer, directory creation or API client."""
    from . import identities, integrity
    from .identity_models import Registry
    registry_path = root / 'output/identities/identity_registry.json'
    registry = read(registry_path, Registry)
    report_path = root / 'output/audit/financial_integrity.json'
    saved_report = json.loads(report_path.read_bytes())
    report = integrity.run(root, write_output=False)
    require_integrity(report)
    if report != saved_report:
        raise ValueError(f'{report_path}: saved integrity report is stale; run the integrity stage')
    verified_registry = identities.run(root, write_output=False)
    if verified_registry != registry:
        raise ValueError(f'{registry_path}: current source mappings are missing or stale; run the identities stage')
    print(f'  {report["passed_checks"]} integrity checks passed; '
          f'{len(registry["legacy_mappings"])} identity mappings validated', flush=True)


def run_stage(stage, root, banks=BANKS, allow_api=False):
    """Adapt existing runner signatures; financial rules remain in their own modules."""
    paths = [root / f'output/reconciled/{bank}_reconciled.json' for bank in BANKS]
    if stage == 'bank':
        from .validation import write_json
        for bank in banks:
            module = import_module('.' + bank, __package__)
            transactions = getattr(module, 'parse_' + bank)(source_file(root, bank))
            report = write_json(transactions, root / f'output/bank/{bank}_transactions.json')
            print(f'  {bank}: {report["validated"]} transactions; '
                  f'{len(report["duplicate_pairs"])} duplicate pairs', flush=True)
    elif stage == 'events':
        events_stage(root, banks, allow_api)
    elif stage == 'reconcile':
        from .reconcile import run
        for bank in banks:
            run(root / f'output/bank/{bank}_transactions.json', root / f'output/events/{bank}_events.json',
                root / f'output/reconciled/{bank}_reconciled.json')
    elif stage == 'relationships':
        from .relationships import run
        run(paths, root / 'output/relationships/relationships.json')
    elif stage == 'classification':
        from .classification import run
        run(paths, root / 'output/relationships/relationships.json', root / 'output/classification/effective_transactions.json')
    elif stage in ('audit', 'obligations', 'settlements', 'identities', 'integrity'):
        result = import_module('.' + stage, __package__).run(root)
        if stage == 'integrity':
            require_integrity(result)
    else:
        raise ValueError(f'unknown stage: {stage}')


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)  # Help exits here, before loading stages or touching project files.
    if args.command == 'run':
        first, last = STAGES.index(args.first), STAGES.index(args.last)
        if first > last:
            cli.error('--from must not come after --to in pipeline order')
        stages = STAGES[first:last + 1]
    else:
        stages = (args.command,)
    root = args.root.resolve()
    stage = args.command
    try:
        if not root.is_dir():
            raise ValueError(f'project root does not exist: {root}')
        for index, stage in enumerate(stages, 1):
            print(f'[{index}/{len(stages)}] {stage}', flush=True)
            if stage == 'validate':
                validate_all(root)
            else:
                selected = getattr(args, 'bank', 'all')
                run_stage(stage, root, BANKS if selected == 'all' else (selected,), getattr(args, 'allow_api', False))
        print('Completed.', flush=True)
        return 0
    except (OSError, ValueError, ImportError) as exc:
        print(f'ERROR [{stage}]: {exc}', file=sys.stderr)
    except Exception as exc:
        print(f'ERROR [{stage}]: unexpected {type(exc).__name__}; execution stopped', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
