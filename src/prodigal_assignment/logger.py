import json
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

_EVENTS = ContextVar('events', default=None)
SENSITIVE = {'phone', 'email', 'full_name', 'notes', 'linkedin_url', 'github_url'}


def begin_run():
    events = []
    _EVENTS.set(events)
    return events


def _sanitize(data):
    if isinstance(data, dict):
        return {str(k): ('[REDACTED]' if k.lower() in SENSITIVE else _sanitize(v))
                for k, v in data.items() if k.lower() != 'authorization'}
    if isinstance(data, list):
        return [_sanitize(x) for x in data]
    if isinstance(data, str):
        data = re.sub(r'\+\d{8,15}', '[PHONE]', data)
        data = re.sub(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b', '[EMAIL]', data)
    return data


def log_event(kind, **payload):
    events = _EVENTS.get()
    if events is not None:
        events.append({'time': datetime.now(timezone.utc).isoformat(),
                       'kind': kind, **_sanitize(payload)})


def save_run(candidate_id, messages, status, events=None, error=None):
    folder = Path('runs')
    folder.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    transcript = []
    for msg in messages:
        entry = {'role': msg.type, 'content': _sanitize(str(msg.content))}
        if getattr(msg, 'tool_calls', None):
            entry['tool_calls'] = _sanitize(msg.tool_calls)
        if msg.type == 'tool':
            entry['tool_name'] = msg.name
            entry['tool_call_id'] = msg.tool_call_id
        transcript.append(entry)
    data = {'candidate_id': candidate_id, 'timestamp': timestamp.isoformat(),
            'final_status': status, 'error': str(error) if error else None,
            'conversation': transcript, 'events': events if events is not None else _EVENTS.get() or []}
    path = folder / f'{candidate_id}_{timestamp:%Y%m%d_%H%M%S_%f}.json'
    path.write_text(json.dumps(data, indent=2, default=str), encoding='utf-8')
    print(f'[LOG] {path}')
    return path
