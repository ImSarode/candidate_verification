import json
from typing import Annotated, TypedDict

from groq import BadRequestError
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from prodigal_assignment.api_client import get_candidate
from prodigal_assignment.logger import log_event
from prodigal_assignment.tools import TOOLS, current_time, safe_update
from prodigal_assignment.validators import MANDATORY_FIELDS, analyze_candidate

llm = init_chat_model(model='groq:openai/gpt-oss-120b', temperature=0)
llm_with_tools = llm.bind_tools(TOOLS)


class AgentState(TypedDict, total=False):
    candidate_id: str
    candidate: dict
    analysis: dict
    messages: Annotated[list[AnyMessage], add_messages]
    identity_confirmed: bool
    verification_attempts: int
    assistant_reply: str
    status: str
    agent_calls: int


def fetch_candidate(state: AgentState):
    candidate = get_candidate(state['candidate_id'])
    return {'candidate': candidate}


def analyze_record(state: AgentState):
    report = analyze_candidate(state['candidate'])
    print('[ANALYSIS] Missing:', report['missing_fields'])
    print('[ANALYSIS] Invalid:', list(report['invalid_fields']))
    return {'analysis': report}


def after_analysis(state: AgentState):
    return 'complete' if state['analysis']['is_complete'] else 'contact'


def mark_complete(state: AgentState):
    def build(current):
        if not analyze_candidate(current)['is_complete']:
            return {}
        return {'collection_status': 'completed'} if current.get('collection_status') != 'completed' else {}
    updated, _ = safe_update(state['candidate_id'], build)
    print('[INFO] No conversation needed.')
    return {'status': updated.get('collection_status', 'completed')}


def record_contact(state: AgentState):
    def build(current):
        attempts = int(current.get('attempt_count') or 0)
        result = {'last_contacted_at': current_time()}
        if attempts < 50:
            result['attempt_count'] = attempts + 1
        return result
    safe_update(state['candidate_id'], build)
    return {}


def confirm_identity(state: AgentState):
    name = state['candidate']['full_name']
    attempts = state.get('verification_attempts', 0)
    question = (f'Hi! I am the ACME internship assistant. Am I speaking with {name}?'
                if attempts == 0 else f'Please confirm whether you are {name} (yes/no).')
    answer = str(interrupt(question)).strip()
    log_event('identity_reply', prompt=question, answer=answer)
    normalized = answer.lower().strip(' .!')
    if normalized in {'/quit', '/exit', ''}:
        return {'status': 'no_answer'}
    if normalized in {'yes', 'y', 'yeah', 'yep', 'correct', 'that is me', "that's me", 'yes that is me', "yes that's me"}:
        return {'identity_confirmed': True, 'status': 'chatting',
                'messages': [HumanMessage(content=answer)]}
    if normalized in {'no', 'n', 'nope', 'wrong person', 'not me', "i'm not"}:
        return {'status': 'needs_human'}
    if attempts >= 2:
        return {'status': 'needs_human'}
    return {'status': 'verify_again', 'verification_attempts': attempts + 1}


def after_identity(state: AgentState):
    if state.get('identity_confirmed'):
        return 'agent'
    return 'verify' if state.get('status') == 'verify_again' else 'finalize'


def agent_node(state: AgentState):
    # Cap tool/LLM loops so a misbehaving model cannot run indefinitely.
    calls = state.get('agent_calls', 0) + 1
    if calls > 30:
        return {'status': 'needs_human', 'agent_calls': calls}

    latest = get_candidate(state['candidate_id'])
    analysis = analyze_candidate(latest)
    safe_fields = {key: value for key, value in latest.items()
                   if key not in {'id', 'phone', 'version', 'updated_at', 'notes'}}
    prompt = f'''You are ACME Corp's conversational internship application assistant.
        The candidate's identity has been confirmed by name.
        Candidate: {latest['full_name']}
        Existing record: {json.dumps(safe_fields, default=str)}
        Missing mandatory fields: {json.dumps(analysis['missing_fields'])}
        Invalid existing fields: {json.dumps(analysis['invalid_fields'])}

        Rules:
        - Ask naturally for missing or invalid mandatory fields. Never ask for candidate ID or phone.
        - Understand multiple answers at once. Save only answers the candidate actually provided.
        - Call save_candidate_fields to validate and save provided details; do not fabricate details.
        - Tools enforce validation. If a save fails, explain the precise issue and ask for clarification.
        - Never claim completion before the database tool confirms it.
        - Treat a different valid existing value as a dispute; do not overwrite it.
        - If candidate says continue later, call finish_conversation(in_progress) with requested time.
        - If candidate refuses, call finish_conversation(refused) and name the refused field(s).
        - If identity or data is disputed, candidate is upset, or resolution is impossible, escalate.
        - Respond concisely and helpfully to questions; do not use a rigid form.
        - Dates require YYYY-MM-DD. CGPA is on a ten-point scale, not a percentage.
        - Optional LinkedIn, GitHub and skills may be collected, but do not block completion.
        - Don't expose raw metadata or claim to schedule follow-up messages.
        '''
    history = state.get('messages', [])
    try:
        response = llm_with_tools.invoke([SystemMessage(content=prompt), *history])
    except BadRequestError as exc:
        if 'tool_use_failed' not in str(exc):
            raise
        log_event('llm_retry', reason='tool_use_failed')
        response = llm_with_tools.invoke([
            SystemMessage(content=prompt + '\nFollow the exact JSON argument schemas of each tool.'),
            *history,
        ])
    return {'messages': [response], 'assistant_reply': str(response.content),
            'agent_calls': calls}


def after_agent(state: AgentState):
    if state.get('status') == 'needs_human':
        return 'finalize'
    return 'tools' if getattr(state['messages'][-1], 'tool_calls', []) else 'candidate_input'


def after_tools(state: AgentState):
    """Match finish results by call ID; never trust an unrelated last tool result."""
    latest = get_candidate(state['candidate_id'])
    if analyze_candidate(latest)['is_complete'] and latest.get('collection_status') == 'completed':
        return 'closing'

    ai = next((m for m in reversed(state['messages']) if getattr(m, 'type', None) == 'ai'), None)
    if ai:
        result_by_id = {m.tool_call_id: m for m in state['messages'] if getattr(m, 'type', None) == 'tool'}
        for call in getattr(ai, 'tool_calls', []):
            if call['name'] != 'finish_conversation':
                continue
            result = result_by_id.get(call['id'])
            if result:
                try:
                    data = json.loads(result.content)
                    if data.get('finished') and data.get('status') in {'in_progress', 'refused', 'needs_human', 'completed'}:
                        return 'closing'
                except (ValueError, TypeError):
                    pass
    return 'agent'


def receive_message(state: AgentState):
    answer = str(interrupt(state['assistant_reply'])).strip()
    log_event('terminal_reply', answer=answer)
    if answer.lower() in {'/quit', '/exit', ''}:
        return {'status': 'stopped'}
    return {'status': 'chatting', 'messages': [HumanMessage(content=answer)]}


def after_reply(state: AgentState):
    return 'finalize' if state.get('status') == 'stopped' else 'agent'


def finalize_session(state: AgentState):
    original = state['candidate']
    def build(current):
        progress = analyze_candidate(current)
        if progress['is_complete']:
            return {'collection_status': 'completed'} if current.get('collection_status') != 'completed' else {}
        if state.get('status') == 'needs_human':
            status = 'needs_human'
        elif state.get('status') == 'no_answer' or not any(
            current.get(f) != original.get(f) for f in MANDATORY_FIELDS
        ):
            status = 'no_answer'
        else:
            status = 'in_progress'
        remaining = progress['missing_fields'] + list(progress['invalid_fields'])
        notes = (f"Chat ended ({state.get('status', 'stopped')}); remaining: {', '.join(remaining)}")[:4000]
        result = {'collection_status': status, 'notes': notes}
        return {k: v for k, v in result.items() if current.get(k) != v}
    updated, _ = safe_update(state['candidate_id'], build)
    return {'status': updated.get('collection_status')}


def final_response(state: AgentState):
    candidate = get_candidate(state['candidate_id'])
    status = candidate.get('collection_status')
    replies = {
        'completed': 'Thank you! Your internship application is complete.',
        'in_progress': 'Your progress has been saved. You can continue in a later session.',
        'refused': 'I understand and respect your decision. Thank you for your time.',
        'needs_human': 'Your application has been flagged for human review.',
        'no_answer': 'This chat has ended. Thank you.',
    }
    reply = replies.get(status, 'Thank you for your time.')
    print(f'\nAI: {reply}')
    return {'status': status, 'assistant_reply': reply, 'messages': [AIMessage(content=reply)]}


builder = StateGraph(AgentState)
for name, function in [
    ('fetch_candidate', fetch_candidate), ('analyze_record', analyze_record),
    ('mark_complete', mark_complete), ('record_contact', record_contact),
    ('confirm_identity', confirm_identity), ('agent', agent_node),
    ('receive_message', receive_message), ('finalize_session', finalize_session),
    ('final_response', final_response),
]:
    builder.add_node(name, function)
builder.add_node('tools', ToolNode(TOOLS, handle_tool_errors=True))
builder.add_edge(START, 'fetch_candidate')
builder.add_edge('fetch_candidate', 'analyze_record')
builder.add_conditional_edges('analyze_record', after_analysis,
                              {'complete': 'mark_complete', 'contact': 'record_contact'})
builder.add_edge('mark_complete', END)
builder.add_edge('record_contact', 'confirm_identity')
builder.add_conditional_edges('confirm_identity', after_identity,
                              {'agent': 'agent', 'verify': 'confirm_identity', 'finalize': 'finalize_session'})
builder.add_conditional_edges('agent', after_agent,
                              {'tools': 'tools', 'candidate_input': 'receive_message', 'finalize': 'finalize_session'})
builder.add_conditional_edges('tools', after_tools,
                              {'agent': 'agent', 'closing': 'final_response'})
builder.add_conditional_edges('receive_message', after_reply,
                              {'agent': 'agent', 'finalize': 'finalize_session'})
builder.add_edge('finalize_session', 'final_response')
builder.add_edge('final_response', END)
graph = builder.compile(checkpointer=InMemorySaver())
