"""
agent/guardrails.py — AWS Bedrock Guardrails v2.1

Version History:
  v1.0 — Initial: blocks DDL/DML, SQL injection, off-topic
  v2.0 — Removed off-topic policy (too aggressive)
  v2.1 — Reduced examples per topic (AWS limit = 5 per topic)

AWS Limit: Maximum 5 examples per topic policy
"""

import boto3
import os
import json
from dotenv import load_dotenv

load_dotenv()

AWS_REGION        = os.getenv("AWS_REGION", "us-east-1")
GUARDRAIL_VERSION = "2.1"


def create_guardrail() -> dict:
    """
    Creates Bedrock Guardrail v2.1.
    Fixed: Reduced examples to 5 per topic (AWS quota limit).
    """
    client = boto3.client("bedrock", region_name=AWS_REGION)

    response = client.create_guardrail(
        name        = "redshift-ai-guardrail-v2",
        description = (
            "Guardrail v2.1 — blocks DDL/DML and SQL injection only. "
            "Off-topic policy removed — was blocking valid DB queries."
        ),

        # ── Topic Policy — max 5 examples per topic ───────────
        topicPolicyConfig={
            "topicsConfig": [

                # Block 1 — Explicit DDL/DML SQL commands
                {
                    "name": "block-ddl-dml",
                    "definition": (
                        "Explicit SQL requests to modify, delete, drop, "
                        "or alter database objects or data including "
                        "DROP, DELETE, INSERT, UPDATE, ALTER, TRUNCATE."
                    ),
                    "examples": [
                        "DROP TABLE orders",
                        "DELETE FROM customers WHERE id = 1",
                        "INSERT INTO orders VALUES (1, 2, 3)",
                        "UPDATE products SET price = 100",
                        "ALTER TABLE orders ADD COLUMN test TEXT",
                    ],
                    "type": "DENY"
                },

                # Block 2 — SQL Injection
                {
                    "name": "block-sql-injection",
                    "definition": (
                        "Attempts to inject malicious SQL code to bypass "
                        "security or access unauthorized data through "
                        "crafted input strings."
                    ),
                    "examples": [
                        "'; DROP TABLE users; --",
                        "1=1 OR '1'='1",
                        "UNION SELECT password FROM users",
                        "exec xp_cmdshell('dir')",
                        "' OR 1=1--",
                    ],
                    "type": "DENY"
                },
            ]
        },

        # ── Content filters ───────────────────────────────────
        contentPolicyConfig={
            "filtersConfig": [
                {"type": "HATE",     "inputStrength": "HIGH",   "outputStrength": "HIGH"},
                {"type": "INSULTS",  "inputStrength": "MEDIUM", "outputStrength": "HIGH"},
                {"type": "VIOLENCE", "inputStrength": "HIGH",   "outputStrength": "HIGH"},
            ]
        },

        # ── PII Redaction ─────────────────────────────────────
        sensitiveInformationPolicyConfig={
            "piiEntitiesConfig": [
                {"type": "EMAIL",                    "action": "ANONYMIZE"},
                {"type": "PHONE",                    "action": "ANONYMIZE"},
                {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "BLOCK"},
                {"type": "AWS_ACCESS_KEY",           "action": "BLOCK"},
                {"type": "AWS_SECRET_KEY",           "action": "BLOCK"},
            ]
        },

        # ── Blocked messages ──────────────────────────────────
        blockedInputMessaging=(
            "I can only help with database exploration and SELECT queries. "
            "Data modification requests (INSERT, UPDATE, DELETE, DROP, ALTER) "
            "are not allowed."
        ),
        blockedOutputsMessaging=(
            "The response was blocked by security policy. "
            "Please rephrase your database question."
        ),
    )

    guardrail_id      = response["guardrailId"]
    guardrail_version = response["version"]

    print(f"✅ Guardrail v{GUARDRAIL_VERSION} created!")
    print(f"   ID     : {guardrail_id}")
    print(f"   Version: {guardrail_version}")
    print(f"\n   ⚠️  Add to your .env file:")
    print(f"   BEDROCK_GUARDRAIL_ID={guardrail_id}")
    print(f"   BEDROCK_GUARDRAIL_VERSION={guardrail_version}")

    return {
        "guardrailId":      guardrail_id,
        "guardrailVersion": guardrail_version,
    }


def get_guardrail_config():
    """Returns guardrail config if configured in .env."""
    guardrail_id      = os.getenv("BEDROCK_GUARDRAIL_ID")
    guardrail_version = os.getenv("BEDROCK_GUARDRAIL_VERSION", "DRAFT")

    if not guardrail_id:
        print("⚠️  No BEDROCK_GUARDRAIL_ID in .env — guardrails disabled.")
        return None

    return {
        "guardrailIdentifier": guardrail_id,
        "guardrailVersion":    guardrail_version,
    }


def delete_guardrail(guardrail_id: str) -> bool:
    """Deletes a guardrail by ID."""
    try:
        client = boto3.client("bedrock", region_name=AWS_REGION)
        client.delete_guardrail(guardrailIdentifier=guardrail_id)
        print(f"✅ Guardrail {guardrail_id} deleted.")
        print(f"   Clear BEDROCK_GUARDRAIL_ID from .env next.")
        return True
    except Exception as e:
        print(f"❌ Error deleting guardrail: {str(e)}")
        return False


def list_guardrails() -> list:
    """Lists all guardrails in your AWS account."""
    client     = boto3.client("bedrock", region_name=AWS_REGION)
    response   = client.list_guardrails()
    guardrails = response.get("guardrails", [])
    for g in guardrails:
        print(f"  ID: {g['guardrailId']} | Name: {g['name']} | Status: {g['status']}")
    return guardrails


if __name__ == "__main__":
    print("Creating Bedrock Guardrail v2.1...")
    result = create_guardrail()
    print(json.dumps(result, indent=2))
