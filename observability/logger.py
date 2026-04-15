"""
observability/logger.py — CloudWatch Logging

What this logs:
  - Every user query with timestamp and session
  - Which tool was called and result
  - Response time per query
  - Errors with full stack trace
  - Token usage (approximate)

CloudWatch Log Groups:
  /redshift-ai/queries     — all user queries
  /redshift-ai/tools       — tool invocations
  /redshift-ai/errors      — errors and exceptions

Free tier: 5GB ingestion/month, 3 dashboards — more than enough for POC.
"""

import boto3
import json
import time
import os
import traceback
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

AWS_REGION    = os.getenv("AWS_REGION", "us-east-1")
LOG_GROUP     = "/redshift-ai/queries"
TOOL_GROUP    = "/redshift-ai/tools"
ERROR_GROUP   = "/redshift-ai/errors"
_cw_client    = None


def _get_client():
    global _cw_client
    if _cw_client is None:
        _cw_client = boto3.client("logs", region_name=AWS_REGION)
    return _cw_client


def setup_log_groups():
    """Creates CloudWatch log groups. Run once via setup_bedrock.py."""
    client = _get_client()
    for group in [LOG_GROUP, TOOL_GROUP, ERROR_GROUP]:
        try:
            client.create_log_group(logGroupName=group)
            # Retain logs for 30 days (free tier friendly)
            client.put_retention_policy(
                logGroupName    = group,
                retentionInDays = 30,
            )
            print(f"✅ Log group created: {group}")
        except client.exceptions.ResourceAlreadyExistsException:
            print(f"ℹ️  Log group exists: {group}")
        except Exception as e:
            print(f"⚠️  Could not create log group {group}: {str(e)}")


def _put_log(log_group: str, stream_name: str, message: dict):
    """Sends a log event to CloudWatch."""
    try:
        client = _get_client()

        # Create stream if it doesn't exist
        try:
            client.create_log_stream(
                logGroupName  = log_group,
                logStreamName = stream_name,
            )
        except client.exceptions.ResourceAlreadyExistsException:
            pass

        client.put_log_events(
            logGroupName  = log_group,
            logStreamName = stream_name,
            logEvents     = [{
                "timestamp": int(time.time() * 1000),
                "message":   json.dumps(message),
            }],
        )
    except Exception as e:
        # Never crash the app due to logging failure
        print(f"⚠️  CloudWatch log failed (non-critical): {str(e)}")


def log_query(session_id: str, query: str, response: str,
              duration_ms: float, model_id: str):
    """Logs a complete query-response cycle."""
    stream = datetime.utcnow().strftime("%Y/%m/%d")
    _put_log(LOG_GROUP, stream, {
        "event":       "query",
        "session_id":  session_id,
        "timestamp":   datetime.utcnow().isoformat(),
        "query":       query[:500],          # truncate long queries
        "response":    response[:500],
        "duration_ms": round(duration_ms, 2),
        "model_id":    model_id,
        "query_len":   len(query),
        "response_len": len(response),
    })


def log_tool_call(session_id: str, tool_name: str,
                  tool_input: dict, tool_result: str, duration_ms: float):
    """Logs every tool invocation by the agent."""
    stream = datetime.utcnow().strftime("%Y/%m/%d")
    _put_log(TOOL_GROUP, stream, {
        "event":       "tool_call",
        "session_id":  session_id,
        "timestamp":   datetime.utcnow().isoformat(),
        "tool_name":   tool_name,
        "tool_input":  str(tool_input)[:300],
        "tool_result": str(tool_result)[:300],
        "duration_ms": round(duration_ms, 2),
    })


def log_error(session_id: str, error: Exception, context: str = ""):
    """Logs errors with full stack trace."""
    stream = datetime.utcnow().strftime("%Y/%m/%d")
    _put_log(ERROR_GROUP, stream, {
        "event":      "error",
        "session_id": session_id,
        "timestamp":  datetime.utcnow().isoformat(),
        "error_type": type(error).__name__,
        "error_msg":  str(error),
        "context":    context,
        "traceback":  traceback.format_exc()[-1000:],
    })


def log_guardrail_block(session_id: str, query: str, block_reason: str):
    """Logs when a Guardrail blocks a request."""
    stream = datetime.utcnow().strftime("%Y/%m/%d")
    _put_log(LOG_GROUP, stream, {
        "event":        "guardrail_blocked",
        "session_id":   session_id,
        "timestamp":    datetime.utcnow().isoformat(),
        "query":        query[:300],
        "block_reason": block_reason,
    })
