import os
import uuid
import requests
from dotenv import load_dotenv

from prodigal_assignment.logger import log_event

load_dotenv()
BASE_URL = os.getenv('CANDIDATE_BASE_URL', '').rstrip('/')
API_KEY = os.getenv('CANDIDATE_API_KEY', '')

class VersionConflict(Exception):
    """The If-Match version no longer matches the record."""


def _configuration():
    if not BASE_URL or not API_KEY:
        raise RuntimeError('Set CANDIDATE_BASE_URL and CANDIDATE_API_KEY in .env')
    return {'Authorization': f'Bearer {API_KEY}', 'Accept': 'application/json'}


def _request(method, path, *, fields=None, version=None, idempotency_key=None):
    headers = _configuration()
    if method == 'PATCH':
        headers.update({
            'Content-Type': 'application/json',
            'If-Match': f'"{version}"',
            'Idempotency-Key': idempotency_key or str(uuid.uuid4()),
        })
    # Never send the Authorization header to the event logger.
    log_event('api_request', method=method, path=path, fields=fields,
              version=version, idempotency_key=headers.get('Idempotency-Key'))
    try:
        response = requests.request(method, f'{BASE_URL}{path}', headers=headers,
                                    json=fields, timeout=15)
        try:
            body = response.json()
        except ValueError:
            body = {'message': response.text[:500]}
        log_event('api_response', method=method, path=path,
                  status_code=response.status_code, response=body)
        if response.status_code == 409:
            raise VersionConflict('The candidate record changed (HTTP 409).')
        response.raise_for_status()
        return body
    except requests.RequestException as exc:
        log_event('api_error', method=method, path=path,
                  error=f'{type(exc).__name__}: {exc}')
        raise


def get_candidate(candidate_id: str) -> dict:
    return _request('GET', f'/v1/candidates/{candidate_id}')


def get_all_candidates() -> dict:
    return _request('GET', '/v1/candidates')


def get_schema() -> dict:
    # The assignment documents this endpoint as public; use the same client.
    return _request('GET', '/v1/schema')


def patch_candidate(candidate_id: str, fields: dict, version: int,
                    idempotency_key: str | None = None) -> dict:
    if not fields:
        raise ValueError('Refusing empty PATCH')
    if any(k in {'id', 'phone', 'version', 'updated_at'} for k in fields):
        raise ValueError('Refusing read-only field update')
    return _request('PATCH', f'/v1/candidates/{candidate_id}', fields=fields,
                    version=version, idempotency_key=idempotency_key)
