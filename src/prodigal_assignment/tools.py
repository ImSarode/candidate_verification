import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field

from .api_client import get_candidate, patch_candidate, VersionConflict
from .validators import (
    MANDATORY_FIELDS, OPTIONAL_FIELDS, analyze_candidate, validate_field, is_missing,
)

EDITABLE_FIELDS = (set(MANDATORY_FIELDS) - {'phone'}) | OPTIONAL_FIELDS


def current_time():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def safe_update(candidate_id, build_changes):
    """Reconcile 409 conflicts; never blindly overwrite a newer record.

    A timed-out PATCH has unknown outcome: re-read rather than automatically
    issuing a duplicate write. Each logical PATCH gets one UUID.
    """
    for _ in range(3):
        current = get_candidate(candidate_id)
        changes = build_changes(current)
        if not changes:
            return current, False
        key = str(uuid.uuid4())
        try:
            patch_candidate(candidate_id, changes, current['version'], idempotency_key=key)
            return get_candidate(candidate_id), True
        except VersionConflict:
            continue
    raise VersionConflict('Repeated version conflicts; requires human review')


def require_identity(runtime: ToolRuntime):
    state = runtime.state
    if not state.get('identity_confirmed'):
        raise PermissionError('Identity must be confirmed first')
    return state['candidate_id']


class FieldsPayload(BaseModel):
    fields: dict[str, Any] = Field(description=(
        'Only explicitly supplied information, e.g. '
        '{"email":"student@example.com", "cgpa":8.5}. '
        'Never infer or invent values.'
    ))


@tool
def validate_candidate_field(field: str, value: str) -> dict:
    """Check the candidate's answer for a mandatory or optional field."""
    if field not in EDITABLE_FIELDS:
        return {'valid': False, 'error': 'Unsupported or read-only field'}
    ok, normalized, error = validate_field(field, value)
    return {'field': field, 'valid': ok, 'normalized_value': normalized, 'error': error}


@tool(args_schema=FieldsPayload)
def save_candidate_fields(fields: dict[str, Any], runtime: ToolRuntime) -> dict:
    """Validate and PATCH only newly supplied candidate fields; never overwrite valid conflicting values."""
    candidate_id = require_identity(runtime)
    if not fields:
        return {'saved': False, 'error': 'No fields supplied'}
    if any(field not in EDITABLE_FIELDS for field in fields):
        return {'saved': False, 'error': 'Unknown or read-only field included'}

    def changes(current):
        out, errors = {}, {}
        for field, value in fields.items():
            if value is None:
                errors[field] = 'Null is not a valid new answer'
                continue
            ok, normalized, error = validate_field(field, value)
            if not ok:
                errors[field] = error
                continue
            existing = current.get(field)
            if not is_missing(existing):
                old_valid, old_normalized, _ = validate_field(field, existing)
                if old_valid:
                    if old_normalized != normalized:
                        errors[field] = 'Existing valid value differs; clarify or escalate'
                    continue
            out[field] = normalized
        if errors:
            raise ValueError(str(errors))
        report = analyze_candidate({**current, **out})
        if report['is_complete'] and current.get('collection_status') != 'completed':
            out['collection_status'] = 'completed'
        elif out and current.get('collection_status') not in {'in_progress', 'completed'}:
            out['collection_status'] = 'in_progress'
        return out

    try:
        updated, changed = safe_update(candidate_id, changes)
        report = analyze_candidate(updated)
        return {'saved': changed, 'is_complete': report['is_complete'],
                'collection_status': updated.get('collection_status'),
                'remaining_missing': report['missing_fields'],
                'remaining_invalid': report['invalid_fields']}
    except (ValueError, VersionConflict) as exc:
        return {'saved': False, 'error': str(exc), 'action': 'Clarify or request human review'}


@tool
def get_collection_progress(runtime: ToolRuntime) -> dict:
    """Read up-to-date missing/invalid mandatory fields and database status."""
    candidate = get_candidate(require_identity(runtime))
    report = analyze_candidate(candidate)
    return {'missing_fields': report['missing_fields'], 'invalid_fields': report['invalid_fields'],
            'is_complete': report['is_complete'],
            'collection_status': candidate.get('collection_status')}


@tool
def finish_conversation(
    status: Literal['in_progress', 'refused', 'needs_human'],
    notes: str,
    runtime: ToolRuntime,
) -> dict:
    """End chat for later continuation, explicit refusal, or escalation. Include specifics in notes."""
    candidate_id = require_identity(runtime)
    if not notes.strip():
        return {'finished': False, 'error': 'Notes are required'}

    def changes(current):
        report = analyze_candidate(current)
        if report['is_complete']:
            return {'collection_status': 'completed'} if current.get('collection_status') != 'completed' else {}
        remaining = report['missing_fields'] + list(report['invalid_fields'])
        new_notes = (notes.strip() + '\nRemaining: ' + ', '.join(remaining))[:4000]
        result = {'collection_status': status, 'notes': new_notes}
        return {key: value for key, value in result.items() if current.get(key) != value}

    try:
        updated, _ = safe_update(candidate_id, changes)
        return {'finished': True, 'status': updated.get('collection_status')}
    except VersionConflict as exc:
        return {'finished': False, 'error': str(exc)}


TOOLS = [validate_candidate_field, save_candidate_fields,
         get_collection_progress, finish_conversation]
