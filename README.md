# Autonomous AI Agent for Candidate Information Collection

A Python-based AI agent that helps complete internship candidate applications. It retrieves a candidate record from the provided ACME REST API, detects **missing and invalid required fields**, confirms the candidate's identity by name, collects corrections through natural conversation, validates answers, and safely updates the record.

## Features

- **Record analysis:** Detects both empty and invalid existing fields before starting a conversation.
- **Identity gate:** Confirms the candidate's name before accepting changes.
- **Conversational collection:** Uses Groq-hosted `openai/gpt-oss-120b` through LangChain to ask relevant follow-up questions and extract multiple answers in one response.
- **Tool-controlled writes:** The LLM requests tools; Python validates input and performs API calls.
- **Safe updates:** Uses HTTP `PATCH`, `If-Match` version checks, and idempotency keys. On HTTP 409, the agent re-reads the record and recalculates changes.
- **Outcome handling:** Supports `completed`, `in_progress`, `refused`, `no_answer`, and `needs_human` as appropriate.
- **Audit logs:** Saves conversation messages, tool calls/results, and sanitized API events as JSON under `runs/`.

## Tech stack

| Technology | Purpose |
|---|---|
| Python | Application logic and CLI |
| LangGraph | Stateful workflow, branching, interruptions, and tool execution |
| LangChain | LLM integration and tool definitions |
| Groq | LLM inference for `openai/gpt-oss-120b` |
| Pydantic and Python validators | Deterministic field validation |
| Requests | REST API integration (GET and PATCH) |
| Pytest | Automated tests |
| uv | Python dependencies and execution |

## Project structure

```text
prodigal_assignment/
├── src/
│   └── prodigal_assignment/
│       ├── __init__.py
│       ├── main.py          # Command-line entry point
│       ├── agent.py         # LangGraph workflow and LLM
│       ├── tools.py         # Callable validation/save/status tools
│       ├── validators.py    # Mandatory-field validation rules
│       ├── api_client.py    # ACME REST API client
│       └── logger.py        # Conversation and API event logging
├── tests/
│   └── test_validators.py
├── runs/                 
├── .env
├── .gitignore
├── pyproject.toml
├── requirements.txt        
└── README.md
```

## Setup

### Prerequisites

- Python **3.11+**
- [uv](https://docs.astral.sh/uv/) (recommended)
- ACME candidate API URL and API key
- Groq API key

## Running the agent

Start a conversation with a candidate ID:

```powershell
uv run python -m prodigal_assignment.main --candidate-id c004
```

The program retrieves the candidate, checks required fields, and either finishes an already-complete record or asks for identity confirmation. Respond naturally at the `You:` prompt. Type **`/quit`** to end the session through the graph's finalization flow.

Example interaction (illustrative, based on a tested `c004` scenario):

```text
AI: Am I speaking with Sneha Iyer?
You: Yes
AI: Could you share your college, degree and branch?
You: ABCD, BSc, CS
AI: Could you confirm the degree format? The allowed value is B.Sc.
You: B.Sc
AI: Thank you! Your internship application is complete.
```

The agent's exact wording can vary. In the recorded `c004` run, `BSc` was rejected by validation, the agent clarified the degree, then successfully saved the required data and received a `completed` status.

## How it works

```text
CLI: candidate ID
       |
       v
 Fetch candidate (GET)
       |
       v
 Analyze mandatory fields
       |
       +-- All valid --> Mark completed (if needed) --> END
       |
       v
 Record contact attempt
       |
       v
 Confirm identity by name
       |
       +-- Wrong/unclear --> Finalize / needs_human
       |
       v
 Groq LLM <---------------------------+
       |                                |
       +-- Ask question --> interrupt   |
       |                    |           |
       |                CLI reply ------+
       |
       +-- Tool call --> ToolNode --> Validate / GET / PATCH
                            |
                            +--> Continue or finalize
```

The workflow's `AgentState` stores the candidate ID, record analysis, identity status, and message history. `main.py` uses `graph.invoke()` to start the graph, and uses `Command(resume=...)` with the **same thread ID** to continue after `interrupt()`.

### Module responsibilities

- **`main.py`:** Parses `--candidate-id`, handles terminal input, resumes interrupted execution, and saves the run.
- **`agent.py`:** Creates the LangGraph state machine, initializes the Groq model, binds tools, routes messages to `ToolNode`, and decides when to finish.
- **`tools.py`:** Exposes `validate_candidate_field`, `save_candidate_fields`, `get_collection_progress`, and `finish_conversation`; enforces identity and write constraints.
- **`validators.py`:** Validates required fields without relying on LLM judgment.
- **`api_client.py`:** Calls the existing ACME REST API; it does **not** create a new REST API server.
- **`logger.py`:** Writes structured JSON run artifacts, with sensitive API response fields redacted.

### Validation and write safety

Required field checks include email format, E.164 phone format (read-only), valid degree/location values, graduation year **2026–2028**, CGPA **0–10**, allowed availability dates, and internship duration **2, 3, or 6 months**.

The agent does not blindly overwrite an existing valid field. Each update validates proposed values and uses the latest candidate record. A `VersionConflict` signals that the server rejected an outdated version; the update function re-fetches and recomputes the patch before retrying. An uncertain network timeout should **not** be blindly replayed as a new write.

## Testing

Run the available automated tests:

```powershell
uv run python -m pytest -q
```

In a previous local check, **3 tests passed**; run the command on your current branch to verify the latest code. These unit tests do not by themselves prove that the live Groq/API integration is working.

Recommended manual evaluation scenarios:

| Candidate | Scenario |
|---|---|
| `c002` | Collect some data, then request to continue later |
| `c003` / `c006` | Already valid record; skip unnecessary conversation |
| `c004` | Collect college, degree and branch; clarify invalid degree formatting |
| `c005` | Check invalid-email correction (reset test data first if already completed) |
| `c007` | Multiple missing fields and multi-turn collection |
| `c008` | Invalid graduation year and missing CGPA |

Check the API's candidate record after each live test to verify the fields and `collection_status`. The assignment provides a `POST /v1/reset` endpoint for testing; use it deliberately and only in your assigned workspace.

## Logs and sample runs

Each run can generate a timestamped `runs/<candidate_id>_<timestamp>.json` file containing the final outcome, conversation/tool messages, and API request/response metadata.

- `c004` has a verified example of a complete conversation with validation recovery.
- A previously captured `c005` run was an **already-complete, no-chat** case; it is useful as a skip-chat test, **not** as a second conversation transcript.
- A `c002` log was subsequently created locally, but its contents have not been reviewed in this README. Verify it before describing its outcome.

---

**Summary:** A tool-using, stateful AI assistant that collects and validates internship candidate information through natural dialogue while maintaining controlled REST API updates and an auditable execution history.
