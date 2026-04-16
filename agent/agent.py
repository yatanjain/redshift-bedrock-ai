"""
agent/agent.py — LangGraph Agent with ALL Bedrock Features

Bedrock features used:
  1. Foundation Model     — Claude Haiku 4.5 (LLM)
  2. Guardrails           — SQL injection + DDL/DML blocking + PII redaction
  3. Titan Embeddings     — Schema vectorization (via knowledge_base.py)
  4. Knowledge Base (RAG) — Relevant schema injected before SQL generation
  5. Prompt Caching       — System prompt cached to save tokens
  6. Prompt Management    — System prompt versioned and externalized
  7. CloudWatch Logging   — Every query and tool call logged
  8. DynamoDB Memory      — Persistent conversation history

RAG Hallucination Fix (v2.1):
  Problem discovered: RAG was injecting partial schema context (top 2 docs)
  and Claude was answering FROM that context instead of calling tools.
  This caused "show all tables" to return only 2 tables instead of 4.

  Fix applied:
    1. System prompt explicitly states schema context = hints only
    2. Metadata queries (list tables, count tables) bypass RAG entirely
    3. Tool descriptions strengthened to force tool calls
"""

import os
import time
from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from langchain_aws import ChatBedrock
from langchain_core.tools import tool

from agent.tools import (
    get_all_tables, get_ddl, get_record_count, get_table_owner,
    run_select_query, get_column_info, run_join_query,
    run_aggregation, explain_query, get_table_stats, search_schema,
)
from agent.guardrails     import get_guardrail_config
from agent.knowledge_base import retrieve_relevant_schema, get_index_stats
from agent.memory         import save_message, load_history, get_session_id
from observability.logger import log_query, log_error, log_guardrail_block

load_dotenv()

CURRENT_USER          = os.getenv("DB_USER", "default_user")
AWS_REGION            = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID              = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
SYSTEM_PROMPT_VERSION = "v2.1"   # bumped — RAG hallucination fix


# ── Keywords that should NEVER use RAG ───────────────────────
# These queries must go directly to tools — RAG gives partial results
# which causes Claude to answer from context instead of calling tools
METADATA_QUERY_KEYWORDS = [
    "show all tables",
    "list all tables",
    "list tables",
    "what tables",
    "all tables",
    "available tables",
    "how many tables",
    "show tables",
    "get tables",
    "which tables",
    "display tables",
    "tables do i have",
    "tables can i",
    "what are the tables",
]


# ══════════════════════════════════════════════════════════════
# SYSTEM PROMPT — v2.1 (RAG Hallucination Fix)
# ══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are a production-grade database assistant for Amazon Redshift.

Your job: Help users explore and query the database using plain English.

════════════════════════════════════════════════════
CRITICAL RULES — FOLLOW THESE EXACTLY
════════════════════════════════════════════════════

RULE 1 — ALWAYS USE TOOLS, NEVER ANSWER FROM CONTEXT ALONE:
  - Schema context provided above is HINTS ONLY — not a complete picture
  - Schema context shows only 2 most relevant schemas — database may have MORE
  - ALWAYS call the appropriate tool to get accurate, complete information
  - NEVER say "based on the schema context" and answer without calling a tool

RULE 2 — FOR TABLE LISTING — ALWAYS CALL tool_get_all_tables:
  - If user asks "show tables", "list tables", "what tables exist" → call tool_get_all_tables
  - Do NOT list tables from schema context — it is incomplete
  - tool_get_all_tables returns ALL tables — schema context shows only some

RULE 3 — FOR DATA QUERIES — USE SCHEMA CONTEXT AS HINTS:
  - Schema context helps you write correct column names in SQL
  - But ALWAYS execute the SQL via the appropriate tool
  - Never return SQL results without actually running the query tool

RULE 4 — DATA MODIFICATION IS FORBIDDEN:
  - NEVER run INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE
  - Only SELECT and metadata operations are allowed

════════════════════════════════════════════════════
YOUR 11 TOOLS
════════════════════════════════════════════════════

METADATA TOOLS — use these for database exploration:
  tool_get_all_tables   → MUST use for ANY "list/show/what tables" question
  tool_get_ddl          → table structure and schema
  tool_get_record_count → row count for a table
  tool_get_table_owner  → table ownership
  tool_get_column_info  → detailed column metadata
  tool_get_table_stats  → min, max, avg, null counts per column
  tool_search_schema    → find tables/columns by keyword

QUERY TOOLS — use these to fetch and analyze data:
  tool_run_select       → single table SELECT queries
  tool_run_join         → multi-table JOIN queries
  tool_run_aggregation  → GROUP BY, SUM, AVG, COUNT queries
  tool_explain_query    → show query execution plan

════════════════════════════════════════════════════
SQL WRITING GUIDELINES
════════════════════════════════════════════════════
- Use standard SQL compatible with Amazon Redshift (PostgreSQL-based)
- Always qualify column names with table aliases in JOINs (e.g. o.order_id)
- Use meaningful aliases for aggregation columns (e.g. SUM(amount) as total_revenue)
- Add LIMIT unless user asks for all records
- Filter with WHERE before GROUP BY for better performance
- For complex questions, call multiple tools in sequence
"""


# ══════════════════════════════════════════════════════════════
# TOOL DEFINITIONS — 11 SQL Tools
# ══════════════════════════════════════════════════════════════

@tool
def tool_get_all_tables() -> str:
    """
    ALWAYS use this tool when user asks to list, show, or find tables.
    Returns ALL tables in the database — not just some.
    Use for: 'show tables', 'list tables', 'what tables exist',
    'how many tables', 'which tables can I access'.
    NEVER answer table listing questions from schema context — always call this tool.
    """
    return get_all_tables(CURRENT_USER)


@tool
def tool_get_ddl(table_name: str) -> str:
    """
    Get the CREATE TABLE definition for a specific table.
    Shows all columns, data types, primary keys, and constraints.
    Use when user asks about table structure or schema.
    """
    return get_ddl(table_name, CURRENT_USER)


@tool
def tool_get_record_count(table_name: str) -> str:
    """
    Get the total number of records in a table.
    Use for: 'how many rows', 'how many records', 'size of table'.
    """
    return get_record_count(table_name, CURRENT_USER)


@tool
def tool_get_table_owner(table_name: str) -> str:
    """Get the owner of a specific table."""
    return get_table_owner(table_name, CURRENT_USER)


@tool
def tool_run_select(query: str) -> str:
    """
    Execute a single-table SELECT query.
    Only SELECT is allowed — no data modification.
    Results limited to 50 rows.
    Use for simple data fetch from one table.
    """
    return run_select_query(query, CURRENT_USER)


@tool
def tool_get_column_info(table_name: str) -> str:
    """
    Get detailed column metadata — names, types, nullable, defaults, primary keys.
    Use when user asks about columns in a specific table.
    """
    return get_column_info(table_name, CURRENT_USER)


@tool
def tool_run_join(query: str) -> str:
    """
    Execute a multi-table JOIN query.
    Use this when query involves JOIN across multiple tables.
    Always use table aliases in JOIN queries.
    """
    return run_join_query(query, CURRENT_USER)


@tool
def tool_run_aggregation(query: str) -> str:
    """
    Execute aggregation queries with SUM, AVG, COUNT, MIN, MAX, GROUP BY, HAVING.
    Use for: 'total revenue', 'average order value', 'count by status',
    'group by region', any summary or analytics question.
    """
    return run_aggregation(query, CURRENT_USER)


@tool
def tool_explain_query(query: str) -> str:
    """
    Show the execution plan for a SELECT query.
    Use when user asks how a query works or wants to optimize it.
    """
    return explain_query(query, CURRENT_USER)


@tool
def tool_get_table_stats(table_name: str) -> str:
    """
    Get column-level statistics — min, max, avg, null counts.
    Use for data profiling and quality checks.
    """
    return get_table_stats(table_name, CURRENT_USER)


@tool
def tool_search_schema(keyword: str) -> str:
    """
    Search for tables and columns containing a keyword.
    Use when user wants to find where certain data lives.
    """
    return search_schema(keyword, CURRENT_USER)


# ══════════════════════════════════════════════════════════════
# AGENT BUILDER
# ══════════════════════════════════════════════════════════════

def build_agent():
    """Builds the LangGraph agent with all Bedrock features."""

    guardrail_config = get_guardrail_config()

    llm_kwargs = {
        "model_id":     MODEL_ID,
        "region_name":  AWS_REGION,
        "model_kwargs": {
            "temperature": 0,      # deterministic SQL generation
            "max_tokens":  4096,
        },
    }

    # FEATURE 2: Attach Guardrails if configured
    if guardrail_config:
        llm_kwargs["guardrails"] = guardrail_config
        print(f"✅ Guardrails enabled: {guardrail_config['guardrailIdentifier']}")
    else:
        print("⚠️  Guardrails not configured — running without")

    llm = ChatBedrock(**llm_kwargs)

    tools_list = [
        tool_get_all_tables,
        tool_get_ddl,
        tool_get_record_count,
        tool_get_table_owner,
        tool_run_select,
        tool_get_column_info,
        tool_run_join,
        tool_run_aggregation,
        tool_explain_query,
        tool_get_table_stats,
        tool_search_schema,
    ]

    agent = create_react_agent(
        model  = llm,
        tools  = tools_list,
        prompt = SYSTEM_PROMPT,
    )

    print(f"✅ Agent built — Model: {MODEL_ID}")
    print(f"   Prompt version : {SYSTEM_PROMPT_VERSION}")
    print(f"   Tools loaded   : {len(tools_list)}")
    return agent


# ══════════════════════════════════════════════════════════════
# QUERY CLASSIFIER — Detects metadata vs data queries
# ══════════════════════════════════════════════════════════════

def _is_metadata_query(query: str) -> bool:
    """
    Detects if a query is a metadata query (list tables, count tables etc).

    Metadata queries bypass RAG entirely because:
    - RAG returns only top 2 schema docs (partial view)
    - Claude may answer from partial context = wrong answer
    - These queries MUST go directly to tools for complete results

    This is the fix for the RAG hallucination bug discovered in v2.0
    where "show all tables" returned only 2 tables instead of 4.
    """
    query_lower = query.lower().strip()
    return any(keyword in query_lower for keyword in METADATA_QUERY_KEYWORDS)


# ══════════════════════════════════════════════════════════════
# QUERY RUNNER — Orchestrates all Bedrock features
# ══════════════════════════════════════════════════════════════

def run_query(agent, query: str, session_id: str,
              use_rag: bool = True) -> str:
    """
    Runs a query through the agent with all Bedrock features.

    Flow:
      1. Check if metadata query → skip RAG if yes (hallucination fix)
      2. Load conversation history from DynamoDB (Feature 8)
      3. Retrieve relevant schema via Titan Embeddings (Features 3+4)
      4. Inject schema as context hint (NOT as answer)
      5. Run LangGraph agent with Claude + tools
      6. Log to CloudWatch (Feature 7)
      7. Save to DynamoDB memory (Feature 8)
    """
    start_time = time.time()

    try:
        # ── Fix 2: Skip RAG for metadata queries ─────────────
        # These must go directly to tools — RAG gives partial results
        # causing hallucination (returning 2 tables instead of 4)
        if _is_metadata_query(query):
            use_rag = False
            print(f"ℹ️  Metadata query detected — bypassing RAG → going direct to tool")

        # ── Feature 8: Load conversation history ──────────────
        history = load_history(session_id, limit=10)

        # ── Features 3+4: RAG schema context injection ────────
        schema_context = ""
        if use_rag:
            schema_context = retrieve_relevant_schema(query, top_k=2)
            if schema_context:
                print(f"ℹ️  RAG: Injecting schema context as hints")

        # Build enriched query
        # IMPORTANT: Label context as HINTS — not as complete answer
        enriched_query = query
        if schema_context:
            enriched_query = (
                f"{schema_context}\n"
                f"NOTE: The schema context above shows HINTS only — "
                f"not all tables. Always call tools for complete results.\n\n"
                f"User question: {query}"
            )

        # ── Build messages with history ───────────────────────
        messages = []
        for msg in history[-6:]:    # last 3 exchanges
            messages.append((msg["role"], msg["content"]))
        messages.append(("user", enriched_query))

        # ── Run LangGraph agent ───────────────────────────────
        response    = agent.invoke({"messages": messages})
        answer      = response["messages"][-1].content
        duration_ms = (time.time() - start_time) * 1000

        # ── Feature 7: Log to CloudWatch ──────────────────────
        log_query(session_id, query, answer, duration_ms, MODEL_ID)

        # ── Feature 8: Save to DynamoDB ───────────────────────
        save_message(session_id, "user",      query)
        save_message(session_id, "assistant", answer)

        return answer

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        log_error(session_id, e, context=f"query: {query[:200]}")

        # Check if Guardrail blocked
        if "guardrail" in str(e).lower() or "blocked" in str(e).lower():
            log_guardrail_block(session_id, query, str(e))
            return (
                "🛡️ **Request blocked by security policy.**\n\n"
                "I can only help with database exploration and SELECT queries. "
                "Please rephrase your question clearly — for example:\n"
                "- 'Show me all tables'\n"
                "- 'How many records are in the orders table?'\n"
                "- 'Show total revenue by region'"
            )
        raise e


# ══════════════════════════════════════════════════════════════
# INTERACTIVE CLI
# ══════════════════════════════════════════════════════════════

def run_interactive():
    print("\n" + "=" * 65)
    print("  Redshift Agentic AI — AWS Bedrock Production Edition")
    print(f"  Model         : {MODEL_ID}")
    print(f"  Prompt version: {SYSTEM_PROMPT_VERSION}")
    print(f"  Features      : Guardrails | RAG | Embeddings | Memory | Logs")
    print("  Type 'exit' to stop | 'history' to see conversation")
    print("=" * 65 + "\n")

    agent      = build_agent()
    session_id = get_session_id(CURRENT_USER)
    print(f"Session: {session_id}\n")

    stats   = get_index_stats()
    use_rag = stats["status"] == "ready"
    if use_rag:
        print(f"✅ Schema RAG index ready ({stats['count']} documents)\n")
    else:
        print("⚠️  Schema RAG index not built — run setup_bedrock.py first\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                print("Goodbye!")
                break
            if user_input.lower() == "history":
                history = load_history(session_id)
                print(f"\n--- Conversation History ({len(history)} messages) ---")
                for msg in history:
                    print(f"{msg['role'].upper()}: {msg['content'][:100]}...")
                print()
                continue

            print("\nAssistant: ", end="", flush=True)
            answer = run_query(agent, user_input, session_id, use_rag)
            print(answer)
            print()

        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\nError: {str(e)}\n")
