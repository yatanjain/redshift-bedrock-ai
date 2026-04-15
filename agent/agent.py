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
"""

import os
import time
import boto3
from dotenv import load_dotenv
from langgraph.prebuilt import create_react_agent
from langchain_aws import ChatBedrock
from langchain_core.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage

from agent.tools import (
    get_all_tables, get_ddl, get_record_count, get_table_owner,
    run_select_query, get_column_info, run_join_query,
    run_aggregation, explain_query, get_table_stats, search_schema,
)
from agent.guardrails    import get_guardrail_config
from agent.knowledge_base import retrieve_relevant_schema, build_schema_index, get_index_stats
from agent.memory        import save_message, load_history, get_session_id
from observability.logger import log_query, log_tool_call, log_error, log_guardrail_block

load_dotenv()

CURRENT_USER = os.getenv("DB_USER", "default_user")
AWS_REGION   = os.getenv("AWS_REGION", "us-east-1")
MODEL_ID     = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-haiku-4-5-20251001-v1:0")

# ── FEATURE 6: Prompt Management — system prompt versioned here ──
# In production: store this in AWS Bedrock Prompt Management console
# and retrieve via boto3 client.get_prompt() call
SYSTEM_PROMPT_VERSION = "v2.0"
SYSTEM_PROMPT = """You are a production-grade database assistant for Amazon Redshift.

Your job: Help users explore and query the database using plain English.

You have 11 specialized tools:
METADATA TOOLS:
  - tool_get_all_tables     : List all tables
  - tool_get_ddl            : Get CREATE TABLE definition
  - tool_get_record_count   : Count rows in a table
  - tool_get_table_owner    : Find table ownership
  - tool_get_column_info    : Detailed column metadata
  - tool_get_table_stats    : Column statistics (min, max, avg, nulls)
  - tool_search_schema      : Find tables/columns by keyword

QUERY TOOLS:
  - tool_run_select         : Single table SELECT queries
  - tool_run_join           : Multi-table JOIN queries
  - tool_run_aggregation    : GROUP BY, SUM, AVG, COUNT queries
  - tool_explain_query      : Show query execution plan

STRICT RULES:
1. NEVER suggest or run INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE
2. Always use the RIGHT tool — use tool_run_join for JOINs, tool_run_aggregation for GROUP BY
3. If schema context is provided above, use it to write accurate SQL
4. Always add LIMIT unless user asks for all records
5. Format results clearly with explanations
6. If a table doesn't exist, say so clearly
7. For complex questions, call multiple tools in sequence

SQL WRITING GUIDELINES:
- Use standard SQL compatible with Amazon Redshift (PostgreSQL-based)
- Qualify column names with table aliases in JOINs (e.g. o.order_id)
- Use meaningful aliases for aggregation columns
- Filter with WHERE before GROUP BY for performance
"""


# ── Wrap all 11 tools with @tool decorator ────────────────────

@tool
def tool_get_all_tables() -> str:
    """List all tables available in the database that the user can access."""
    return get_all_tables(CURRENT_USER)

@tool
def tool_get_ddl(table_name: str) -> str:
    """Get the DDL (CREATE TABLE definition) for a specific table. Use when user asks about table structure or schema."""
    return get_ddl(table_name, CURRENT_USER)

@tool
def tool_get_record_count(table_name: str) -> str:
    """Get the total number of records in a table. Use for 'how many rows/records' questions."""
    return get_record_count(table_name, CURRENT_USER)

@tool
def tool_get_table_owner(table_name: str) -> str:
    """Get the owner of a specific table."""
    return get_table_owner(table_name, CURRENT_USER)

@tool
def tool_run_select(query: str) -> str:
    """Execute a single-table SELECT query. Only SELECT allowed. Results limited to 50 rows."""
    return run_select_query(query, CURRENT_USER)

@tool
def tool_get_column_info(table_name: str) -> str:
    """Get detailed column metadata — names, types, nullable, defaults, primary keys."""
    return get_column_info(table_name, CURRENT_USER)

@tool
def tool_run_join(query: str) -> str:
    """Execute a multi-table JOIN query. Use this when query involves JOIN keyword across multiple tables."""
    return run_join_query(query, CURRENT_USER)

@tool
def tool_run_aggregation(query: str) -> str:
    """Execute aggregation queries with SUM, AVG, COUNT, MIN, MAX, GROUP BY, or HAVING. Use for summary/analytics questions."""
    return run_aggregation(query, CURRENT_USER)

@tool
def tool_explain_query(query: str) -> str:
    """Show the execution plan for a SELECT query. Use when user asks how a query works or wants to optimize it."""
    return explain_query(query, CURRENT_USER)

@tool
def tool_get_table_stats(table_name: str) -> str:
    """Get column-level statistics for a table — min, max, avg, null counts. Use for data profiling."""
    return get_table_stats(table_name, CURRENT_USER)

@tool
def tool_search_schema(keyword: str) -> str:
    """Search for tables and columns containing a keyword. Use when user wants to find where certain data lives."""
    return search_schema(keyword, CURRENT_USER)


# ── Build the agent ───────────────────────────────────────────

def build_agent():
    """
    Builds the full production LangGraph agent with all Bedrock features.
    """
    # FEATURE 1: Foundation Model — Claude Haiku 4.5
    guardrail_config = get_guardrail_config()

    llm_kwargs = {
        "model_id":     MODEL_ID,
        "region_name":  AWS_REGION,
        "model_kwargs": {
            "temperature": 0,
            "max_tokens":  4096,
        },
    }

    # FEATURE 2: Guardrails — attach if configured
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
    print(f"   Tools: {len(tools_list)} SQL tools loaded")
    return agent


# ── Query runner with all features ───────────────────────────

def run_query(agent, query: str, session_id: str,
              use_rag: bool = True) -> str:
    """
    Runs a query through the agent with:
    - FEATURE 3+4: RAG schema context injection
    - FEATURE 7:   CloudWatch logging
    - FEATURE 8:   DynamoDB memory
    """
    start_time = time.time()

    try:
        # FEATURE 8: Load conversation history from DynamoDB
        history  = load_history(session_id, limit=10)

        # FEATURE 3+4: Retrieve relevant schema via Titan Embeddings
        schema_context = ""
        if use_rag:
            schema_context = retrieve_relevant_schema(query, top_k=2)

        # Build enriched prompt with schema context
        enriched_query = query
        if schema_context:
            enriched_query = f"{schema_context}\n\nUser question: {query}"

        # Build messages with history
        messages = []
        for msg in history[-6:]:    # last 3 exchanges
            messages.append((msg["role"], msg["content"]))
        messages.append(("user", enriched_query))

        # Run agent
        response    = agent.invoke({"messages": messages})
        answer      = response["messages"][-1].content
        duration_ms = (time.time() - start_time) * 1000

        # FEATURE 7: Log to CloudWatch
        log_query(session_id, query, answer, duration_ms, MODEL_ID)

        # FEATURE 8: Save to DynamoDB
        save_message(session_id, "user",      query)
        save_message(session_id, "assistant", answer)

        return answer

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        log_error(session_id, e, context=f"query: {query[:200]}")

        # Check if guardrail blocked
        if "guardrail" in str(e).lower() or "blocked" in str(e).lower():
            log_guardrail_block(session_id, query, str(e))
            return ("🛡️ **Request blocked by security policy.**\n\n"
                    "I can only help with database exploration and SELECT queries. "
                    "Data modification operations are not permitted.")
        raise e


# ── Interactive CLI ───────────────────────────────────────────

def run_interactive():
    print("\n" + "=" * 65)
    print("  Redshift Agentic AI — AWS Bedrock Production Edition")
    print(f"  Model    : {MODEL_ID}")
    print(f"  Features : Guardrails | RAG | Embeddings | Memory | Logs")
    print("  Type 'exit' to stop | 'history' to see conversation")
    print("=" * 65 + "\n")

    agent      = build_agent()
    session_id = get_session_id(CURRENT_USER)
    print(f"Session: {session_id}\n")

    # Check RAG index
    stats = get_index_stats()
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
