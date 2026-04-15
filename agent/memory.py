"""
agent/memory.py — DynamoDB Persistent Conversation Memory

What this does:
  - Stores conversation history in DynamoDB (free tier: 25GB)
  - Memory persists across browser refreshes and EC2 restarts
  - Each user gets their own conversation history
  - Auto-expires old sessions after 7 days (TTL)

DynamoDB Table Schema:
  PK: session_id (string)   e.g. "user_yatan_20240415"
  SK: timestamp  (string)   e.g. "2024-04-15T10:30:00"
  Attributes:
    - role    : "user" or "assistant"
    - content : message text
    - ttl     : Unix timestamp for auto-expiry (7 days)
"""

import boto3
import os
import json
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

AWS_REGION   = os.getenv("AWS_REGION", "us-east-1")
TABLE_NAME   = os.getenv("DYNAMODB_TABLE", "redshift-ai-memory")
SESSION_TTL_DAYS = 7


def create_memory_table() -> bool:
    """
    Creates the DynamoDB table for conversation memory.
    Run once via setup_bedrock.py.
    Free tier: 25GB storage, 25 read/write units — more than enough.
    """
    client = boto3.client("dynamodb", region_name=AWS_REGION)

    try:
        client.create_table(
            TableName            = TABLE_NAME,
            KeySchema            = [
                {"AttributeName": "session_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp",  "KeyType": "RANGE"},
            ],
            AttributeDefinitions = [
                {"AttributeName": "session_id", "AttributeType": "S"},
                {"AttributeName": "timestamp",  "AttributeType": "S"},
            ],
            BillingMode = "PAY_PER_REQUEST",  # free tier friendly
        )

        # Wait until table is active
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=TABLE_NAME)

        # Enable TTL for auto-cleanup after 7 days
        client.update_time_to_live(
            TableName            = TABLE_NAME,
            TimeToLiveSpecification = {
                "Enabled":       True,
                "AttributeName": "ttl",
            }
        )

        print(f"✅ DynamoDB table '{TABLE_NAME}' created with TTL enabled")
        return True

    except client.exceptions.ResourceInUseException:
        print(f"ℹ️  DynamoDB table '{TABLE_NAME}' already exists")
        return True
    except Exception as e:
        print(f"❌ Error creating DynamoDB table: {str(e)}")
        return False


def save_message(session_id: str, role: str, content: str) -> bool:
    """
    Saves a single message to DynamoDB.

    Args:
        session_id: Unique session identifier (e.g. "user_yatan")
        role:       "user" or "assistant"
        content:    Message text
    """
    try:
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table    = dynamodb.Table(TABLE_NAME)

        ttl = int(time.time()) + (SESSION_TTL_DAYS * 24 * 60 * 60)

        table.put_item(Item={
            "session_id": session_id,
            "timestamp":  datetime.utcnow().isoformat(),
            "role":       role,
            "content":    content,
            "ttl":        ttl,
        })
        return True
    except Exception as e:
        print(f"⚠️  DynamoDB save failed: {str(e)}")
        return False


def load_history(session_id: str, limit: int = 20) -> list[dict]:
    """
    Loads conversation history for a session.

    Args:
        session_id: Session to load
        limit:      Max number of recent messages to return

    Returns:
        List of {"role": ..., "content": ...} dicts
    """
    try:
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table    = dynamodb.Table(TABLE_NAME)

        from boto3.dynamodb.conditions import Key
        response = table.query(
            KeyConditionExpression = Key("session_id").eq(session_id),
            ScanIndexForward       = True,   # oldest first
            Limit                  = limit,
        )

        messages = []
        for item in response.get("Items", []):
            messages.append({
                "role":    item["role"],
                "content": item["content"],
            })
        return messages

    except Exception as e:
        print(f"⚠️  DynamoDB load failed: {str(e)}")
        return []


def clear_history(session_id: str) -> bool:
    """Clears all messages for a session."""
    try:
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table    = dynamodb.Table(TABLE_NAME)

        from boto3.dynamodb.conditions import Key
        response = table.query(
            KeyConditionExpression = Key("session_id").eq(session_id)
        )

        with table.batch_writer() as batch:
            for item in response.get("Items", []):
                batch.delete_item(Key={
                    "session_id": item["session_id"],
                    "timestamp":  item["timestamp"],
                })
        print(f"✅ Cleared history for session: {session_id}")
        return True

    except Exception as e:
        print(f"⚠️  DynamoDB clear failed: {str(e)}")
        return False


def get_session_id(username: str) -> str:
    """Generates a session ID for a user (resets daily)."""
    today = datetime.utcnow().strftime("%Y%m%d")
    return f"{username}_{today}"
