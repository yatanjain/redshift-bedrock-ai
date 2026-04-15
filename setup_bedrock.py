"""
setup_bedrock.py — One-Time AWS Resource Setup

Run this ONCE before starting the app for the first time.
It sets up all AWS resources needed:

  1. DynamoDB table     — conversation memory
  2. CloudWatch groups  — observability logging
  3. Bedrock Guardrail  — security policy
  4. Schema RAG index   — Titan Embeddings index

Usage:
    python setup_bedrock.py

After running, copy the printed values into your .env file.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


def check_aws_credentials():
    """Verify AWS credentials are configured."""
    import boto3
    try:
        sts = boto3.client("sts", region_name=os.getenv("AWS_REGION", "us-east-1"))
        identity = sts.get_caller_identity()
        print(f"✅ AWS credentials valid")
        print(f"   Account: {identity['Account']}")
        print(f"   User:    {identity['Arn']}")
        return True
    except Exception as e:
        print(f"❌ AWS credentials not found: {str(e)}")
        print("\nFix: Add to your .env file:")
        print("  AWS_ACCESS_KEY_ID=your_key")
        print("  AWS_SECRET_ACCESS_KEY=your_secret")
        print("  AWS_REGION=us-east-1")
        return False


def setup_dynamodb():
    print("\n── Step 1: DynamoDB (Conversation Memory) ──────────────")
    from agent.memory import create_memory_table
    create_memory_table()


def setup_cloudwatch():
    print("\n── Step 2: CloudWatch (Observability Logging) ──────────")
    from observability.logger import setup_log_groups
    setup_log_groups()


def setup_guardrail():
    print("\n── Step 3: Bedrock Guardrail (Security) ─────────────────")
    existing_id = os.getenv("BEDROCK_GUARDRAIL_ID")
    if existing_id:
        print(f"ℹ️  Guardrail already configured: {existing_id}")
        print("   Skipping creation. Delete BEDROCK_GUARDRAIL_ID from .env to recreate.")
        return

    from agent.guardrails import create_guardrail
    result = create_guardrail()
    print(f"\n⚠️  ACTION REQUIRED — Add these to your .env file:")
    print(f"   BEDROCK_GUARDRAIL_ID={result['guardrailId']}")
    print(f"   BEDROCK_GUARDRAIL_VERSION={result['guardrailVersion']}")


def setup_rag_index():
    print("\n── Step 4: Schema RAG Index (Titan Embeddings) ──────────")
    from agent.knowledge_base import build_schema_index
    build_schema_index()


def main():
    print("=" * 60)
    print("  Redshift Agentic AI — AWS Setup")
    print("  This script sets up all required AWS resources")
    print("=" * 60)

    # Check credentials first
    if not check_aws_credentials():
        sys.exit(1)

    print("\nRunning setup steps...\n")

    try:
        setup_dynamodb()
    except Exception as e:
        print(f"⚠️  DynamoDB setup failed: {str(e)} — continuing...")

    try:
        setup_cloudwatch()
    except Exception as e:
        print(f"⚠️  CloudWatch setup failed: {str(e)} — continuing...")

    try:
        setup_guardrail()
    except Exception as e:
        print(f"⚠️  Guardrail setup failed: {str(e)} — continuing...")

    try:
        setup_rag_index()
    except Exception as e:
        print(f"⚠️  RAG index setup failed: {str(e)} — continuing...")

    print("\n" + "=" * 60)
    print("✅ Setup complete!")
    print("\nNext steps:")
    print("  1. Copy any printed values into your .env file")
    print("  2. Run: streamlit run app.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
