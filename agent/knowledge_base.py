"""
agent/knowledge_base.py — Bedrock Titan Embeddings + Schema RAG

What this does:
  1. Takes all your table schemas (DDL + descriptions)
  2. Converts them to vectors using Amazon Titan Embeddings V2
  3. Stores vectors in an in-memory ChromaDB (local, free)
  4. At query time: finds the most relevant schemas before SQL generation
  5. Injects relevant schema context into the agent prompt

Why this matters:
  - Agent knows EXACTLY which tables/columns are relevant before generating SQL
  - Prevents hallucinated column names
  - Works like enterprise Bedrock Knowledge Bases but locally free
"""

import boto3
import json
import os
import chromadb
from chromadb.config import Settings
from dotenv import load_dotenv

load_dotenv()

AWS_REGION  = os.getenv("AWS_REGION", "us-east-1")

# Titan Embeddings V2 — free tier, 1536 dimensions
EMBED_MODEL = "amazon.titan-embed-text-v2:0"

# In-memory ChromaDB — no extra setup needed
_chroma_client     = chromadb.Client(Settings(anonymized_telemetry=False))
_schema_collection = None


# ── Schema documents — describes each table in plain English ──
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
Common queries: products by category, price range, inventory levels, supplier lookup
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

2. Orders with returns (left join to include all orders):
   SELECT o.order_id, o.total_amount, r.reason, r.refund_amount
   FROM orders o LEFT JOIN order_returns r ON o.order_id = r.order_id

3. Full order details with customer and returns:
   SELECT c.customer_name, o.product_name, o.total_amount,
          o.status, r.reason
   FROM orders o
   JOIN customers c ON o.customer_id = c.customer_id
   LEFT JOIN order_returns r ON o.order_id = r.order_id

4. Revenue by customer segment:
   SELECT c.segment, SUM(o.total_amount) as revenue
   FROM orders o JOIN customers c ON o.customer_id = c.customer_id
   WHERE o.status = 'Completed'
   GROUP BY c.segment
        """.strip()
    },
]


def _get_bedrock_client():
    return boto3.client("bedrock-runtime", region_name=AWS_REGION)


def _embed_text(text: str) -> list[float]:
    """
    Calls Amazon Titan Embeddings V2 to convert text to a vector.
    Cost: ~$0.00011 per 1K tokens — extremely cheap.
    """
    client   = _get_bedrock_client()
    body     = json.dumps({"inputText": text, "dimensions": 512, "normalize": True})
    response = client.invoke_model(
        modelId     = EMBED_MODEL,
        body        = body,
        contentType = "application/json",
        accept      = "application/json",
    )
    result = json.loads(response["body"].read())
    return result["embedding"]


def build_schema_index():
    """
    Builds the schema vector index using Titan Embeddings.
    Call this once at app startup.
    """
    global _schema_collection

    try:
        _chroma_client.delete_collection("schema_index")
    except Exception:
        pass

    _schema_collection = _chroma_client.create_collection(
        name     = "schema_index",
        metadata = {"description": "Redshift table schemas"},
    )

    print("🔵 Building schema index with Titan Embeddings...")
    embeddings = []
    documents  = []
    ids        = []

    for doc in SCHEMA_DOCUMENTS:
        print(f"   Embedding: {doc['id']}...")
        embedding = _embed_text(doc["content"])
        embeddings.append(embedding)
        documents.append(doc["content"])
        ids.append(doc["id"])

    _schema_collection.add(
        embeddings = embeddings,
        documents  = documents,
        ids        = ids,
    )
    print(f"✅ Schema index built — {len(SCHEMA_DOCUMENTS)} documents indexed")
    return _schema_collection


def retrieve_relevant_schema(query: str, top_k: int = 2) -> str:
    """
    Given a user query, finds the most relevant schema documents.
    Returns schema context to inject into the agent prompt.

    Args:
        query:  User's natural language question
        top_k:  Number of most relevant schemas to return

    Returns:
        Formatted string with relevant schema context
    """
    global _schema_collection

    if _schema_collection is None:
        return ""

    try:
        query_embedding = _embed_text(query)
        results = _schema_collection.query(
            query_embeddings = [query_embedding],
            n_results        = top_k,
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


def get_index_stats() -> dict:
    """Returns stats about the schema index."""
    global _schema_collection
    if _schema_collection is None:
        return {"status": "not built", "count": 0}
    count = _schema_collection.count()
    return {"status": "ready", "count": count, "model": EMBED_MODEL}
