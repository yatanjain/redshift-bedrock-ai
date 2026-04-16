"""
agent/knowledge_base.py — Bedrock Titan Embeddings + Schema RAG

What this does:
  1. AUTO-GENERATES schema documents from your actual database
  2. Converts them to vectors using Amazon Titan Embeddings V2
  3. Stores vectors in ChromaDB (local, free)
  4. At query time: finds most relevant schemas before SQL generation
  5. Injects relevant schema context into the agent prompt

Two modes:
  - AUTO mode : reads schema directly from database (recommended)
  - MANUAL mode: uses hardcoded SCHEMA_DOCUMENTS (fallback)

Why RAG matters:
  - Agent knows EXACTLY which tables/columns exist before generating SQL
  - Prevents hallucinated column names
  - Works for any number of tables — 5 or 500
"""

import boto3
import json
import os
import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv
from agent.database import get_connection

load_dotenv()

AWS_REGION  = os.getenv("AWS_REGION", "us-east-1")

# ── Titan Embeddings V2 ───────────────────────────────────────
# Cost: ~$0.00011 per 1K tokens — extremely cheap
# Dimensions: 512 — good balance of accuracy vs speed
EMBED_MODEL = "amazon.titan-embed-text-v2:0"

# ── ChromaDB — Local In-Memory Vector Store ───────────────────
# Free, no setup needed
# Note: in-memory = rebuilt on every app restart
# For production: use chromadb.PersistentClient(path="./chroma_db")
# For production: use chromadb.PersistentClient(path="./chroma_db")

# ── ChromaDB client — single shared client ────────────────────
# Collections are per-user (see _schema_collections below)
_chroma_client = chromadb.Client(Settings(anonymized_telemetry=False))

# ── Per-user schema collections ───────────────────────────────
# Key: username, Value: ChromaDB collection
# Each user gets their OWN index with ONLY their accessible tables
# This prevents schema leakage across users
_schema_collections = {}

# AUTO SCHEMA GENERATION
# Reads directly from database — works for any number of tables
# ══════════════════════════════════════════════════════════════

def auto_generate_schema_documents(username: str = "default_user") -> list:
    """
    Automatically generates schema documents by reading directly
    from the database. Works for SQLite (POC) and Redshift (prod).

    For each table it generates:
      - Table name and purpose (inferred from name)
      - All column names, types, nullable, primary key info
      - Row count
      - Foreign key relationships (JOIN keys)
      - Common query patterns (inferred from column names)

    Returns:
        List of dicts with 'id' and 'content' keys
        Ready to be embedded and stored in ChromaDB
    """
    try:
        conn   = get_connection(username)
        cursor = conn.cursor()

        # ── Get all tables ────────────────────────────────────
        # ── SQLITE POC (active) ───────────────────────────────
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
            ORDER BY name
        """)
        # ── REDSHIFT EQUIVALENT (commented) ──────────────────
        # Replace the SQLite query above with this for Redshift:
        #
        # cursor.execute("""
        #     SELECT table_name as name
        #     FROM information_schema.tables
        #     WHERE table_schema = 'public'
        #     ORDER BY table_name
        # """)
        #
        # Why this works automatically:
        #   Connecting AS the actual user means Redshift returns
        #   ONLY tables this user has SELECT permission on.
        #   Per-user RAG index becomes permission-scoped automatically!
        #   No USER_PERMISSIONS dict or manual filtering needed.
        # ──────────────────────────────────────────────────────

        all_tables = [row[0] for row in cursor.fetchall()]

        # ── SQLite POC — filter by user permissions ───────────
        # DELETE this filtering block for Redshift
        # Redshift automatically returns only accessible tables
        from agent.database import get_allowed_tables
        allowed_lower = [t.lower() for t in get_allowed_tables(username)]
        tables = [t for t in all_tables if t.lower() in allowed_lower]
        print(f"📊 Found {len(tables)} tables to index...")

        documents       = []
        foreign_keys    = {}   # track FK relationships across tables

        # ── First pass — collect FK info for all tables ───────
        for table in tables:
            cursor.execute(f"PRAGMA foreign_key_list({table})")
            fks = cursor.fetchall()
            if fks:
                foreign_keys[table] = [
                    {
                        "from_col": fk["from"],
                        "to_table": fk["table"],
                        "to_col":   fk["to"],
                    }
                    for fk in fks
                ]

        # ── Second pass — build schema doc per table ─────────
        for table in tables:

            # Get column details
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()

            # Get row count
            try:
                cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                row_count = cursor.fetchone()["cnt"]
            except Exception:
                row_count = 0

            # Build column descriptions
            col_lines  = []
            pk_columns = []
            for col in columns:
                pk_flag      = " (PRIMARY KEY)" if col["pk"] else ""
                null_flag    = "NOT NULL" if col["notnull"] else "NULLABLE"
                default_flag = f", DEFAULT {col['dflt_value']}" if col["dflt_value"] else ""
                col_lines.append(
                    f"  - {col['name']:<20} {col['type']:<12} "
                    f"{null_flag}{pk_flag}{default_flag}"
                )
                if col["pk"]:
                    pk_columns.append(col["name"])

            # Build FK relationship lines
            fk_lines = []
            if table in foreign_keys:
                for fk in foreign_keys[table]:
                    fk_lines.append(
                        f"  - {table}.{fk['from_col']} "
                        f"→ {fk['to_table']}.{fk['to_col']}"
                    )

            # Find reverse FKs (tables that reference this table)
            reverse_fk_lines = []
            for other_table, fks in foreign_keys.items():
                for fk in fks:
                    if fk["to_table"] == table:
                        reverse_fk_lines.append(
                            f"  - {other_table}.{fk['from_col']} "
                            f"→ {table}.{fk['to_col']}"
                        )

            # Infer common query patterns from column names
            col_names    = [col["name"].lower() for col in columns]
            query_hints  = []

            if any("date" in c or "time" in c for c in col_names):
                query_hints.append("date range filters")
            if any("status" in c or "state" in c for c in col_names):
                query_hints.append("filter by status")
            if any("amount" in c or "price" in c or "total" in c or "revenue" in c for c in col_names):
                query_hints.append("revenue and financial aggregations")
            if any("region" in c or "country" in c or "area" in c for c in col_names):
                query_hints.append("geographic analysis")
            if any("category" in c or "type" in c or "segment" in c for c in col_names):
                query_hints.append("grouping and segmentation")
            if any("name" in c for c in col_names):
                query_hints.append("lookup by name")
            if table in foreign_keys or table in [
                fk["to_table"] for fks in foreign_keys.values() for fk in fks
            ]:
                query_hints.append("JOIN with related tables")

            # ── Build the final document text ─────────────────
            content_parts = [
                f"Table: {table}",
                f"Row count: {row_count:,}",
                "",
                "Columns:",
            ]
            content_parts.extend(col_lines)

            if pk_columns:
                content_parts.append(f"\nPrimary Key: {', '.join(pk_columns)}")

            if fk_lines:
                content_parts.append("\nForeign Keys (this table references):")
                content_parts.extend(fk_lines)

            if reverse_fk_lines:
                content_parts.append("\nReferenced by (other tables JOIN here):")
                content_parts.extend(reverse_fk_lines)

            if query_hints:
                content_parts.append(
                    f"\nCommon query patterns: {', '.join(query_hints)}"
                )

            content = "\n".join(content_parts)

            documents.append({
                "id":      f"{table}_schema",
                "content": content,
            })

            print(f"   ✅ {table} ({row_count:,} rows, {len(columns)} columns)")

        # ── Also generate JOIN patterns document ──────────────
        if len(tables) > 1:
            join_doc = _generate_join_patterns_doc(tables, foreign_keys)
            if join_doc:
                documents.append(join_doc)
                print(f"   ✅ join_patterns ({len(foreign_keys)} relationships)")

        conn.close()
        print(f"\n✅ Auto-generated {len(documents)} schema documents from {len(tables)} tables")
        return documents

    except Exception as e:
        print(f"⚠️  Auto schema generation failed: {str(e)}")
        print("   Falling back to manual SCHEMA_DOCUMENTS...")
        return SCHEMA_DOCUMENTS


def _generate_join_patterns_doc(tables: list, foreign_keys: dict) -> dict | None:
    """
    Auto-generates common JOIN patterns based on FK relationships.
    """
    if not foreign_keys:
        return None

    join_examples = []
    join_count    = 0

    for table, fks in foreign_keys.items():
        for fk in fks:
            if join_count >= 6:   # limit to 6 examples
                break

            ref_table = fk["to_table"]
            from_col  = fk["from_col"]
            to_col    = fk["to_col"]

            # Generate table aliases (first letter of each table)
            t_alias   = table[0]
            ref_alias = ref_table[0]

            join_examples.append(f"""
{join_count + 1}. JOIN {table} with {ref_table}:
   SELECT {t_alias}.*, {ref_alias}.*
   FROM {table} {t_alias}
   JOIN {ref_table} {ref_alias}
   ON {t_alias}.{from_col} = {ref_alias}.{to_col}""")

            join_count += 1

    if not join_examples:
        return None

    content = "Common JOIN patterns for this database:\n"
    content += "\n".join(join_examples)
    content += "\n\nAlways use table aliases (e.g. o for orders, c for customers)"
    content += "\nUse LEFT JOIN to include rows with no matching record in other table"

    return {
        "id":      "join_patterns",
        "content": content,
    }


# ══════════════════════════════════════════════════════════════
# MANUAL SCHEMA DOCUMENTS (Fallback / Override)
# Used if auto-generation fails or you want custom descriptions
# ══════════════════════════════════════════════════════════════

SCHEMA_DOCUMENTS = [
    {
        "id":      "orders_schema",
        "content": """
Table: orders
Purpose: Stores all customer orders and purchase transactions.
Columns:
  - order_id      (INTEGER, PK)  : Unique order identifier
  - customer_id   (INTEGER)      : Foreign key to customers table
  - product_name  (TEXT)         : Name of the product ordered
  - quantity      (INTEGER)      : Number of units ordered
  - unit_price    (REAL)         : Price per unit
  - total_amount  (REAL)         : Total order value (quantity * unit_price)
  - order_date    (TEXT)         : Date order was placed (YYYY-MM-DD)
  - region        (TEXT)         : Geographic region (West, East, North, South)
  - status        (TEXT)         : Order status (Completed, Pending, Cancelled)
Common queries: sales by region, orders by status, revenue analysis, date range filters
JOIN key: orders.customer_id -> customers.customer_id
        """.strip()
    },
    {
        "id":      "customers_schema",
        "content": """
Table: customers
Purpose: Stores customer master data and segmentation.
Columns:
  - customer_id   (INTEGER, PK)  : Unique customer identifier
  - customer_name (TEXT)         : Full name of customer
  - email         (TEXT)         : Customer email address
  - country       (TEXT)         : Country of residence
  - segment       (TEXT)         : Business segment (Enterprise, SMB)
  - created_date  (TEXT)         : Date customer was onboarded
Common queries: customer by country, segment analysis, customer lookup
JOIN key: customers.customer_id -> orders.customer_id
        """.strip()
    },
    {
        "id":      "products_schema",
        "content": """
Table: products
Purpose: Product catalog with pricing and inventory.
Columns:
  - product_id    (INTEGER, PK)  : Unique product identifier
  - product_name  (TEXT)         : Product display name
  - category      (TEXT)         : Product category (Electronics, Furniture)
  - unit_price    (REAL)         : Current selling price
  - stock_qty     (INTEGER)      : Current inventory quantity
  - supplier      (TEXT)         : Supplier company name
Common queries: products by category, price range, inventory levels
        """.strip()
    },
    {
        "id":      "order_returns_schema",
        "content": """
Table: order_returns
Purpose: Tracks returned orders and refund amounts.
Columns:
  - return_id     (INTEGER, PK)  : Unique return identifier
  - order_id      (INTEGER)      : Foreign key to orders table
  - return_date   (TEXT)         : Date return was processed
  - reason        (TEXT)         : Reason for return
  - refund_amount (REAL)         : Amount refunded to customer
Common queries: return rates, refund totals, return reasons analysis
JOIN key: order_returns.order_id -> orders.order_id
        """.strip()
    },
    {
        "id":      "join_patterns",
        "content": """
Common JOIN patterns for this database:

1. Orders with customer names:
   SELECT o.order_id, c.customer_name, o.total_amount, o.status
   FROM orders o JOIN customers c ON o.customer_id = c.customer_id

2. Orders with returns:
   SELECT o.order_id, o.total_amount, r.reason, r.refund_amount
   FROM orders o LEFT JOIN order_returns r ON o.order_id = r.order_id

3. Revenue by customer segment:
   SELECT c.segment, SUM(o.total_amount) as revenue
   FROM orders o JOIN customers c ON o.customer_id = c.customer_id
   WHERE o.status = 'Completed'
   GROUP BY c.segment
        """.strip()
    },
]


# ══════════════════════════════════════════════════════════════
# CORE FUNCTIONS
# ══════════════════════════════════════════════════════════════

def _get_bedrock_client():
    """Returns boto3 Bedrock runtime client."""
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)


def _embed_text(text: str) -> list:
    """
    Calls Amazon Titan Embeddings V2 to convert text to a vector.

    Input:  Any text string
    Output: List of 512 floats representing meaning of the text

    Similar meanings → similar vectors → found together in search
    Cost: ~$0.00011 per 1K tokens
    """
    client   = _get_bedrock_client()
    body     = json.dumps({
        "inputText":  text,
        "dimensions": 512,    # vector size — 512 is good balance
        "normalize":  True,   # normalizes vector length for better similarity
    })
    response = client.invoke_model(
        modelId     = EMBED_MODEL,
        body        = body,
        contentType = "application/json",
        accept      = "application/json",
    )


# ══════════════════════════════════════════════════════════════
# CORE FUNCTIONS — Per-User Schema Index
# ══════════════════════════════════════════════════════════════

def build_schema_index(use_auto: bool = True, username: str = "default_user"):
    """
    Builds a PER-USER schema vector index using Titan Embeddings.

    Key change from v1:
      - Each user gets their OWN ChromaDB collection
      - Only tables the user has permission to access are indexed
      - User A cannot see schemas for tables they can't query
      - Prevents schema leakage between users

    Args:
        use_auto: True = auto-generate from DB, False = use SCHEMA_DOCUMENTS
        username: Build index for this specific user
    """
    global _schema_collections

    collection_name = f"schema_index_{username}"

    # Delete old collection for this user if exists
    try:
        _chroma_client.delete_collection(collection_name)
    except Exception:
        pass

    # Create fresh collection for this user
    collection = _chroma_client.create_collection(
        name     = collection_name,
        metadata = {
            "description": f"Schema index for user: {username}",
            "username":    username,
        },
    )

    # Get schema documents — filtered by user permissions
    if use_auto:
        print(f"🔵 Auto-generating schema for user: {username}...")
        documents = auto_generate_schema_documents(username)
    else:
        print(f"🔵 Using manual SCHEMA_DOCUMENTS for user: {username}...")
        # Filter manual docs to only allowed tables
        from agent.database import get_allowed_tables
        allowed = [t.lower() for t in get_allowed_tables(username)]
        documents = [
            doc for doc in SCHEMA_DOCUMENTS
            if any(t in doc["id"].lower() for t in allowed)
            or doc["id"] == "join_patterns"
        ]

    # Embed each document
    print(f"\n🔵 Building vector index with Titan Embeddings V2...")
    embeddings     = []
    documents_text = []
    ids            = []

    for doc in documents:
        print(f"   Embedding: {doc['id']}...")
        embedding = _embed_text(doc["content"])
        embeddings.append(embedding)
        documents_text.append(doc["content"])
        ids.append(doc["id"])

    # Store in user-specific ChromaDB collection
    collection.add(
        embeddings = embeddings,
        documents  = documents_text,
        ids        = ids,
    )

    # Store in per-user dict
    _schema_collections[username] = collection

    print(f"\n✅ Schema index ready for '{username}' — {len(documents)} docs indexed")
    return collection


def retrieve_relevant_schema(query: str, username: str = "default_user",
                              top_k: int = 2) -> str:
    """
    Finds most relevant schema documents for this user's query.
    Uses the user-specific index — only searches their accessible schemas.

    Args:
        query:    User's natural language question
        username: Which user's index to search
        top_k:    Number of most relevant schemas to return
    """
    global _schema_collections

    collection = _schema_collections.get(username)
    if collection is None:
        return ""

    try:
        query_embedding = _embed_text(query)
        results = collection.query(
            query_embeddings = [query_embedding],
            n_results        = min(top_k, collection.count()),
        )

        if not results["documents"] or not results["documents"][0]:
            return ""

        context  = "\n\n=== RELEVANT SCHEMA CONTEXT (from Knowledge Base) ===\n"
        for i, doc in enumerate(results["documents"][0]):
            context += f"\n[Schema {i+1}]\n{doc}\n"
        context += "=== END SCHEMA CONTEXT ===\n"
        return context

    except Exception as e:
        print(f"⚠️  Schema retrieval failed: {str(e)}")
        return ""


def get_index_stats(username: str = "default_user") -> dict:
    """Returns health stats about this user's schema index."""
    global _schema_collections
    collection = _schema_collections.get(username)
    if collection is None:
        return {"status": "not built", "count": 0}
    count = collection.count()
    return {
        "status":   "ready",
        "count":    count,
        "model":    EMBED_MODEL,
        "username": username,
    }


def rebuild_index(username: str = "default_user"):
    """Force rebuilds the schema index for a user."""
    global _schema_collections
    if username in _schema_collections:
        del _schema_collections[username]
    print(f"🔄 Rebuilding schema index for user: {username}...")
    return build_schema_index(use_auto=True, username=username)
