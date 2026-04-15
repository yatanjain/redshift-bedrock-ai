"""
agent/guardrails.py — AWS Bedrock Guardrails

What Guardrails does:
  - Blocks SQL injection attempts at the LLM level
  - Blocks DDL/DML (DROP, DELETE, INSERT, UPDATE) requests
  - Redacts sensitive PII from responses (emails, phone numbers)
  - Blocks off-topic requests (not database-related)
  - Blocks profanity and harmful content

Two modes:
  1. create_guardrail()    — Creates guardrail in AWS (run once)
  2. apply_guardrail()     — Applies to every LLM call at runtime
"""

import boto3
import os
import json
from dotenv import load_dotenv

load_dotenv()

AWS_REGION      = os.getenv("AWS_REGION", "us-east-1")
GUARDRAIL_ID_KEY = "BEDROCK_GUARDRAIL_ID"
GUARDRAIL_VER_KEY = "BEDROCK_GUARDRAIL_VERSION"


def create_guardrail() -> dict:
    """
    Creates a Bedrock Guardrail in your AWS account.
    Run this ONCE via setup_bedrock.py — saves the ID to .env.

    Returns:
        dict with guardrailId and guardrailVersion
    """
    client = boto3.client("bedrock", region_name=AWS_REGION)

    response = client.create_guardrail(
        name="redshift-ai-guardrail",
        description="Guardrail for Redshift Agentic AI — blocks unsafe SQL and off-topic requests",

        # ── Topic policy — block off-topic requests ──
        topicPolicyConfig={
            "topicsConfig": [
                {
                    "name":        "block-ddl-dml",
                    "definition":  "Requests to modify, delete, drop, or alter database objects or data",
                    "examples":    [
                        "Drop the orders table",
                        "Delete all customers",
                        "Insert a new record",
                        "Update prices in products",
                        "ALTER TABLE orders ADD COLUMN",
                    ],
                    "type": "DENY"
                },
                {
                    "name":        "block-sql-injection",
                    "definition":  "Attempts to inject malicious SQL code or bypass security",
                    "examples":    [
                        "'; DROP TABLE users; --",
                        "1=1 OR '1'='1",
                        "UNION SELECT * FROM passwords",
                    ],
                    "type": "DENY"
                },
                {
                    "name":        "block-off-topic",
                    "definition":  "Requests unrelated to database exploration and SQL queries",
                    "examples":    [
                        "Write me a poem",
                        "What is the weather today",
                        "Help me write an email",
                    ],
                    "type": "DENY"
                },
            ]
        },

        # ── Content filters — block harmful content ──
        contentPolicyConfig={
            "filtersConfig": [
                {"type": "HATE",     "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {"type": "INSULTS",  "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {"type": "VIOLENCE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
            ]
        },

        # ── PII redaction — protect sensitive data in responses ──
        sensitiveInformationPolicyConfig={
            "piiEntitiesConfig": [
                {"type": "EMAIL",        "action": "ANONYMIZE"},
                {"type": "PHONE",        "action": "ANONYMIZE"},
                {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "BLOCK"},
                {"type": "AWS_ACCESS_KEY", "action": "BLOCK"},
                {"type": "AWS_SECRET_KEY", "action": "BLOCK"},
            ]
        },

        # ── Blocked messages shown to user ──
        blockedInputMessaging=(
            "I can only help with database exploration and SELECT queries. "
            "Data modification requests (INSERT, UPDATE, DELETE, DROP) are not allowed."
        ),
        blockedOutputsMessaging=(
            "The response was blocked by security policy. "
            "Please rephrase your question about the database."
        ),
    )

    guardrail_id      = response["guardrailId"]
    guardrail_version = response["version"]

    print(f"✅ Guardrail created!")
    print(f"   ID     : {guardrail_id}")
    print(f"   Version: {guardrail_version}")
    print(f"\n   Add to your .env file:")
    print(f"   BEDROCK_GUARDRAIL_ID={guardrail_id}")
    print(f"   BEDROCK_GUARDRAIL_VERSION={guardrail_version}")

    return {"guardrailId": guardrail_id, "guardrailVersion": guardrail_version}


def get_guardrail_config() -> dict | None:
    """
    Returns guardrail config dict if BEDROCK_GUARDRAIL_ID is set in .env.
    Returns None if not configured (guardrails disabled).
    """
    guardrail_id = os.getenv(GUARDRAIL_ID_KEY)
    guardrail_version = os.getenv(GUARDRAIL_VER_KEY, "DRAFT")

    if not guardrail_id:
        print("⚠️  No BEDROCK_GUARDRAIL_ID in .env — guardrails disabled.")
        print("   Run setup_bedrock.py to create a guardrail.")
        return None

    return {
        "guardrailIdentifier": guardrail_id,
        "guardrailVersion":    guardrail_version,
    }


def list_guardrails() -> list:
    """Lists all guardrails in your AWS account."""
    client   = boto3.client("bedrock", region_name=AWS_REGION)
    response = client.list_guardrails()
    return response.get("guardrails", [])


def delete_guardrail(guardrail_id: str) -> bool:
    """Deletes a guardrail by ID."""
    client = boto3.client("bedrock", region_name=AWS_REGION)
    client.delete_guardrail(guardrailIdentifier=guardrail_id)
    print(f"✅ Guardrail {guardrail_id} deleted.")
    return True


if __name__ == "__main__":
    print("Creating Bedrock Guardrail...")
    result = create_guardrail()
    print(json.dumps(result, indent=2))
