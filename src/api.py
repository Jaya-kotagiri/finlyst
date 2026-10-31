"""Local read-only Finlyst API. Start with: python -m src.api"""

import logging
import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from . import api_models as models
from . import db, db_read
from .api_access import local_request
from .output_models import Relationship, SettlementView

LOG = logging.getLogger(__name__)
PREFIX = '/api/v1'


def envelope(data, snapshot_id=None):
    return dict(api_version='4A.1', snapshot_id=str(snapshot_id) if snapshot_id is not None else None, data=data)


def error(status, code, message, fields=()):
    payload = models.ErrorResponse(error=models.ErrorDetail(code=code, message=message, fields=list(fields)))
    return JSONResponse(status_code=status, content=payload.model_dump(), headers={'Cache-Control': 'no-store'})


def query(request, operation, *args, **kwargs):
    """Open one connection after HTTP validation; calculations stay in db_read."""
    try:
        connection = db.connect(request.app.state.project_root)
    except (ValueError, OSError):
        raise HTTPException(503, dict(code='database_unavailable', message='Check the local PostgreSQL server and FINLYST_DB_* configuration.')) from None
    with connection:
        try:
            return operation(connection, *args, **kwargs)
        except ValueError as exc:
            if str(exc).startswith('Snapshot was not found'):
                raise HTTPException(404, dict(code='snapshot_not_found', message='Snapshot not found. Select an ID from /api/v1/snapshots.')) from None
            if str(exc).startswith('Database query failed:'):
                raise HTTPException(503, dict(code='database_unavailable', message='Database query failed. Check PostgreSQL availability and migrations.')) from None
            raise HTTPException(409, dict(code='data_integrity_error', message='Stored snapshot validation failed. Run the db-verify CLI command locally.')) from None


def create_app(root=None):
    app = FastAPI(title='Finlyst read-only API', version='4A.1', debug=False,
        description='Localhost-only financial reads. Select an explicit imported snapshot. Amounts are decimal strings; unknowns remain null.',
        responses={code: {'model': models.ErrorResponse} for code in (403, 404, 405, 409, 422, 500, 503)})
    app.state.project_root = Path(root if root is not None else os.environ.get('FINLYST_PROJECT_ROOT', db.ROOT)).resolve()

    @app.middleware('http')
    async def access_boundary(request, call_next):
        if not local_request(request):
            response = error(403, 'local_access_only', 'This development API accepts same-origin loopback access only.')
        elif request.method not in ('GET', 'HEAD'):
            response = error(405, 'read_only', 'Only read operations are available.')
            response.headers['Allow'] = 'GET, HEAD'
        else:
            try:
                response = await call_next(request)
            except Exception as exc:
                # Do not log query values, bank rows, credentials or exception bodies.
                LOG.error('API request failed (%s)', type(exc).__name__)
                response = error(500, 'internal_error', 'The request could not be completed. Check the local server.')
        response.headers.update({'Cache-Control': 'no-store', 'Pragma': 'no-cache',
                                 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        fields = [models.FieldError(location=[str(part) for part in item['loc']], message=item['msg']) for item in exc.errors()]
        return error(422, 'invalid_request', 'Invalid query or path parameters.', fields)

    @app.exception_handler(ResponseValidationError)
    async def invalid_response(request, exc):
        return error(500, 'response_validation_error', 'Stored data does not match the API response contract.')

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        if isinstance(exc.detail, dict) and 'code' in exc.detail:
            return error(exc.status_code, exc.detail['code'], exc.detail['message'])
        return error(exc.status_code, 'not_found' if exc.status_code == 404 else 'http_error',
                     'Endpoint not found.' if exc.status_code == 404 else 'The request is not supported.')

    def view(request, name, filters):
        return query(request, db_read.read_view, filters.snapshot_id, name, **filters.read_filters())['records']

    @app.get('/health', response_model=models.Envelope[models.Health], response_model_exclude_unset=True)
    def health():
        return envelope(dict(status='ok', mode='local-read-only'))

    @app.get(PREFIX + '/snapshots', response_model=models.Envelope[list[models.SnapshotInfo]], response_model_exclude_unset=True)
    def snapshots(request: Request):
        return envelope(query(request, db_read.list_snapshots))

    @app.get(PREFIX + '/accounts', response_model=models.Envelope[list[models.AccountRecord]], response_model_exclude_unset=True)
    def accounts(request: Request, filters: Annotated[models.Filters, Query()]):
        return envelope(view(request, 'accounts', filters), filters.snapshot_id)

    @app.get(PREFIX + '/transactions', response_model=models.TransactionPage, response_model_exclude_unset=True)
    def transactions(request: Request, filters: Annotated[models.TransactionQuery, Query()]):
        records = view(request, 'transactions', filters)
        page = records[filters.offset:filters.offset + filters.limit]
        end = filters.offset + len(page)
        return dict(envelope(page, filters.snapshot_id), pagination=dict(total=len(records), offset=filters.offset,
            limit=filters.limit, returned=len(page), next_offset=end if end < len(records) else None))

    @app.get(PREFIX + '/transactions/{record_id}', response_model=models.Envelope[models.TransactionRecord], response_model_exclude_unset=True)
    def transaction_detail(request: Request, record_id: UUID, selection: Annotated[models.SnapshotQuery, Query()]):
        records = query(request, db_read.read_view, selection.snapshot_id, 'transactions', record_id=str(record_id))['records']
        if not records:
            raise HTTPException(404, dict(code='transaction_not_found', message='Transaction not found in the selected snapshot.'))
        return envelope(records[0], selection.snapshot_id)

    @app.get(PREFIX + '/summaries', response_model=models.Envelope[models.SummaryResult], response_model_exclude_unset=True)
    def summaries(request: Request, filters: Annotated[models.Filters, Query()]):
        return envelope(query(request, db_read.read_summary, filters.snapshot_id, **filters.read_filters()), filters.snapshot_id)

    @app.get(PREFIX + '/categories', response_model=models.Envelope[list[models.CategoryRecord]], response_model_exclude_unset=True)
    def categories(request: Request, filters: Annotated[models.Filters, Query()]):
        return envelope(view(request, 'categories', filters), filters.snapshot_id)

    @app.get(PREFIX + '/relationships', response_model=models.Envelope[list[models.EvidenceRecord[Relationship]]], response_model_exclude_unset=True)
    def relationships(request: Request, filters: Annotated[models.Filters, Query()]):
        return envelope(view(request, 'relationships', filters), filters.snapshot_id)

    @app.get(PREFIX + '/obligations', response_model=models.Envelope[list[models.ObligationRecord]], response_model_exclude_unset=True)
    def obligations(request: Request, filters: Annotated[models.SettlementQuery, Query()]):
        return envelope(view(request, 'obligations', filters), filters.snapshot_id)

    @app.get(PREFIX + '/settlements', response_model=models.Envelope[list[models.EvidenceRecord[SettlementView]]], response_model_exclude_unset=True)
    def settlements(request: Request, filters: Annotated[models.SettlementQuery, Query()]):
        return envelope(view(request, 'settlements', filters), filters.snapshot_id)

    return app


def main():
    import uvicorn
    host = os.environ.get('FINLYST_API_HOST', '127.0.0.1')
    if host not in ('127.0.0.1', '::1', 'localhost'):
        raise ValueError('FINLYST_API_HOST must be loopback; public deployment requires a separate authenticated design')
    try:
        port = int(os.environ.get('FINLYST_API_PORT', '8000'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise ValueError('FINLYST_API_PORT must be an integer from 1 to 65535') from None
    uvicorn.run(create_app(), host=host, port=port, proxy_headers=False, access_log=False, server_header=False)


if __name__ == '__main__':
    main()
