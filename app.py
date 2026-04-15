"""
app.py — Streamlit Chat UI
Redshift Agentic AI — AWS Bedrock Production Edition
"""

import streamlit as st
import os
import time
from dotenv import load_dotenv
from agent.database      import setup_sample_database
from agent.memory        import get_session_id, load_history, clear_history
from agent.knowledge_base import get_index_stats
from observability.logger import log_error

load_dotenv()

# ── Init database ─────────────────────────────────────────────
if not os.path.exists("poc_database.db"):
    setup_sample_database()

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title = "Redshift AI Assistant",
    page_icon  = "🤖",
    layout     = "wide",
)

# ── Sidebar — Bedrock features status ────────────────────────
with st.sidebar:
    st.title("⚙️ Bedrock Features")
    st.divider()

    model_id    = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-haiku-4-5-20251001-v1:0")
    region      = os.getenv("AWS_REGION", "us-east-1")
    guardrail   = os.getenv("BEDROCK_GUARDRAIL_ID", "")
    dynamo_tbl  = os.getenv("DYNAMODB_TABLE", "redshift-ai-memory")
    db_user     = os.getenv("DB_USER", "default_user")

    st.markdown("**🤖 Foundation Model**")
    st.code(model_id.split("anthropic.")[-1], language=None)

    st.markdown("**🌍 Region**")
    st.code(region, language=None)

    st.markdown("**🛡️ Guardrails**")
    if guardrail:
        st.success(f"Enabled: {guardrail[:12]}...")
    else:
        st.warning("Disabled — run setup_bedrock.py")

    st.markdown("**🧠 Titan Embeddings + RAG**")
    rag_stats = get_index_stats()
    if rag_stats["status"] == "ready":
        st.success(f"Ready — {rag_stats['count']} schemas indexed")
    else:
        st.warning("Not built — run setup_bedrock.py")

    st.markdown("**💾 DynamoDB Memory**")
    st.info(f"Table: {dynamo_tbl}")

    st.markdown("**📊 CloudWatch Logs**")
    st.info("/redshift-ai/queries")

    st.divider()

    # Session info
    session_id = get_session_id(db_user)
    st.markdown(f"**👤 User:** `{db_user}`")
    st.markdown(f"**🔑 Session:** `{session_id}`")

    st.divider()

    # Clear history button
    if st.button("🗑️ Clear Chat History", use_container_width=True):
        clear_history(session_id)
        st.session_state.messages = [{
            "role":    "assistant",
            "content": "Chat history cleared! How can I help you?"
        }]
        st.rerun()

# ── Main UI ───────────────────────────────────────────────────
st.title("🤖 Redshift Agentic AI Assistant")
st.caption("Production-grade database assistant powered by AWS Bedrock")

# ── Example prompts ───────────────────────────────────────────
with st.expander("💡 Example prompts — click to expand"):
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**📋 Explore**")
        st.markdown("""
        - Show me all tables
        - What columns does orders have?
        - Show DDL for customers
        - Who owns the products table?
        - Search schema for 'customer'
        """)
    with col2:
        st.markdown("**🔍 Query**")
        st.markdown("""
        - Top 5 orders from West region
        - Show all completed orders
        - Which customers are from USA?
        - Show orders placed in 2024
        - Find products under $100
        """)
    with col3:
        st.markdown("**📊 Analytics**")
        st.markdown("""
        - Total revenue by region
        - Average order value by segment
        - Count orders by status
        - Join orders with customer names
        - Show return rate by reason
        """)

# ── Initialize agent ──────────────────────────────────────────
@st.cache_resource
def get_agent():
    from agent.agent import build_agent
    return build_agent()

@st.cache_resource
def init_rag():
    from agent.knowledge_base import build_schema_index
    return build_schema_index()

if "agent_ready" not in st.session_state:
    with st.spinner("🔄 Connecting to AWS Bedrock and building schema index..."):
        try:
            st.session_state.ai_agent = get_agent()

            # Build RAG index
            try:
                init_rag()
                st.session_state.rag_ready = True
            except Exception as e:
                st.session_state.rag_ready = False
                st.warning(f"⚠️ Schema RAG unavailable: {str(e)}")

            st.session_state.agent_ready = True

        except Exception as e:
            st.error(f"❌ Failed to connect to AWS Bedrock: {str(e)}")
            st.markdown("""
            **Troubleshooting:**
            - Run `aws configure` or set `AWS_ACCESS_KEY_ID` in `.env`
            - Enable Claude Haiku 4.5 in Bedrock Model Catalog
            - Check `AWS_REGION=us-east-1` in `.env`
            - Run `python setup_bedrock.py` first
            """)
            st.stop()

# ── Chat history ──────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = [{
        "role":    "assistant",
        "content": (
            "👋 Hi! I'm your **Redshift AI Assistant** powered by AWS Bedrock.\n\n"
            "I can help you explore your database using plain English — "
            "schema exploration, SQL queries, JOINs, aggregations, and more!\n\n"
            "What would you like to know about your database?"
        )
    }]

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# ── Chat input ────────────────────────────────────────────────
if prompt := st.chat_input("Ask me about your database..."):

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("🤔 Thinking via AWS Bedrock..."):
            try:
                from agent.agent import run_query
                session_id = get_session_id(os.getenv("DB_USER", "default_user"))
                use_rag    = st.session_state.get("rag_ready", False)

                start = time.time()
                answer = run_query(
                    st.session_state.ai_agent,
                    prompt,
                    session_id,
                    use_rag = use_rag,
                )
                elapsed = time.time() - start

                st.markdown(answer)
                st.caption(f"⏱️ {elapsed:.1f}s | 🤖 {model_id.split('anthropic.')[-1]}")

            except Exception as e:
                error_msg = str(e)
                if "guardrail" in error_msg.lower() or "blocked" in error_msg.lower():
                    answer = ("🛡️ **Request blocked by Bedrock Guardrails.**\n\n"
                              "Only SELECT queries and database exploration are permitted.")
                else:
                    answer = f"❌ Error: {error_msg}"
                st.markdown(answer)

        st.session_state.messages.append({"role": "assistant", "content": answer})
