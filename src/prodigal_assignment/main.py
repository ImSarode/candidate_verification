import argparse
import uuid
from langgraph.types import Command

from .agent import graph
from .api_client import get_candidate
from .logger import begin_run, log_event, save_run
from .tools import safe_update


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-id', required=True)
    args = parser.parse_args()
    config = {'configurable': {'thread_id': str(uuid.uuid4())}}
    events = begin_run()
    status = 'unknown'
    error = None
    print('\n=== ACME Internship AI Agent ===')
    print('Type /quit or press Ctrl+C to end the local chat.\n')
    try:
        result = graph.invoke({
            'candidate_id': args.candidate_id, 'identity_confirmed': False,
            'verification_attempts': 0, 'agent_calls': 0,
            'messages': [], 'status': 'pending',
        }, config=config)
        while '__interrupt__' in result:
            question = result['__interrupt__'][0].value
            print(f'\nAI: {question}')
            log_event('terminal_prompt', prompt=question)
            try:
                answer = input('\nYou: ')
            except (KeyboardInterrupt, EOFError):
                print('\n[INFO] Chat stopped by candidate.')
                answer = '/quit'
            result = graph.invoke(Command(resume=answer), config=config)
        candidate = get_candidate(args.candidate_id)
        status = candidate.get('collection_status', 'unknown')
        print(f'\n--- Session ended: {status} ---')
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        log_event('session_error', error=error)
        print(f'\n[ERROR] {error}')
        # Attempt to persist an escalation only if we established contact.
        try:
            snapshot = graph.get_state(config)
            state = snapshot.values or {}
            if state.get('candidate') and state.get('identity_confirmed'):
                def changes(current):
                    if current.get('collection_status') == 'completed':
                        return {}
                    note = ('Agent interrupted by technical error; resume with human. '
                            + type(exc).__name__)[:4000]
                    return {'collection_status': 'needs_human', 'notes': note}
                updated, _ = safe_update(args.candidate_id, changes)
                status = updated.get('collection_status', 'unknown')
            else:
                status = 'not_persisted'
        except Exception as persist_error:
            status = 'not_persisted'
            log_event('persistence_error', error=str(persist_error))
            print('[WARN] Could not persist final status; check the API before claiming completion.')
    finally:
        try:
            snapshot = graph.get_state(config)
            state = snapshot.values or {}
            save_run(args.candidate_id, state.get('messages', []), status,
                     events=events, error=error)
        except Exception as log_error:
            print(f'[WARN] Could not save transcript: {log_error}')


if __name__ == '__main__':
    main()
