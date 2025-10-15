## ACD - Linear Bulk Issue Assignment Lambda ##
##
## DESCRIPTION:
##   AWS Lambda function that assigns multiple Linear issues to a specified teammate
##   in bulk and posts a summary of the results to a Slack channel. The function
##   fetches internal Linear UUIDs for each issue identifier, performs the assignment,
##   and reports success/failure status via Slack notification.
##
## USAGE:
##   Deploy as an AWS Lambda function. Invoke with an event payload containing:
##   {
##     "issue_ids": ["TEAM-123", "TEAM-456"],  // List of Linear issue identifiers
##     "assignee_id": "linear-user-uuid"        // Linear user UUID to assign issues to
##   }
##
## REQUIREMENTS:
##   - Python 3.8+
##   - requests library
##
## REQUIRED ENVIRONMENT VARIABLES:
##   LINEAR_API_KEY    - Linear API key with issue read/write permissions
##   SLACK_BOT_TOKEN   - Slack bot token with chat:write scope
##
## DEFAULT CONFIGURATION (override via event payload):
##   ASSIGNEE_ID        - Linear user UUID (default: "REPLACE_WITH_LINEAR_USER_ID")
##   ISSUE_IDS          - Array of issue identifiers (default: ["TEAM-123", "TEAM-456", "TEAM-789"])
##   SLACK_CHANNEL_ID   - Target Slack channel ID (default: "REPLACE_WITH_SLACK_CHANNEL_ID")
##
## RETURN VALUE:
##   JSON object with:
##   - linear_results: Array of assignment results for each issue
##   - slack_status: Status of the Slack notification
##   - summary: Success and failure counts
##
## NOTES:
##   - All secrets must be provided via Lambda environment variables
##   - Replace placeholder values before production deployment
##   - Function uses Linear GraphQL API for issue operations
##
################################################################################
import os
import json
import requests
from typing import Optional, Dict, Any, List, Tuple

# --- Configuration ---

# Required: Set these as environment variables in your Lambda configuration
LINEAR_API_KEY = os.environ["LINEAR_API_KEY"]
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")

LINEAR_API_ENDPOINT = "https://api.linear.app/graphql"

# Default values: These can be overridden by the Lambda event payload
ASSIGNEE_ID = "REPLACE_WITH_LINEAR_USER_ID"
ISSUE_IDS = ["TEAM-123", "TEAM-456", "TEAM-789"]
SLACK_CHANNEL_ID = "REPLACE_WITH_SLACK_CHANNEL_ID"

# ------------- Linear helpers -------------

def fetch_issue_internal_id(issue_key: str) -> Optional[str]:
    """
    Fetches the internal Linear UUID for a given public issue key (e.g., "ITPROJ-123").
    Uses the singular `issue` query, which correctly handles public identifiers.
    """
    query = """
    query GetIssueId($identifier: String!) {
      issue(id: $identifier) {
        id
      }
    }
    """
    resp = requests.post(
        LINEAR_API_ENDPOINT,
        headers={"Authorization": LINEAR_API_KEY, "Content-Type": "application/json"},
        json={"query": query, "variables": {"identifier": issue_key}}
    )
    if resp.status_code != 200:
        print(f"Linear HTTP {resp.status_code}: {resp.text}")
        return None
    data = resp.json()
    if "errors" in data:
        print(f"Linear GraphQL errors: {data['errors']}")
        return None

    # The response structure for a singular `issue` query is simpler.
    issue_data = data.get("data", {}).get("issue")
    if issue_data and issue_data.get("id"):
        return issue_data["id"]
        
    return None

def assign_issue(issue_id: str, user_id: str) -> Dict[str, Any]:
    """
    Assigns a Linear issue to a user using their internal IDs.
    """
    mutation = """
    mutation AssignIssue($issueId: String!, $userId: String!) {
      issueUpdate(id: $issueId, input: { assigneeId: $userId }) {
        success
        issue {
          id
          title
          identifier
          assignee { id name }
        }
      }
    }
    """
    resp = requests.post(
        LINEAR_API_ENDPOINT,
        headers={"Authorization": LINEAR_API_KEY, "Content-Type": "application/json"},
        json={"query": mutation, "variables": {"issueId": issue_id, "userId": user_id}}
    )
    return resp.json()

# ------------- Slack helper -------------

def send_slack_message(channel: str, text: str, blocks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Sends a message to a Slack channel using a bot token.
    """
    if not SLACK_BOT_TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN not set in environment variables.")
    payload = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload),
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error: {data.get('error', 'Unknown error')}")
    return data

# ------------- Formatting -------------

def build_slack_message(successes: List[Dict[str, str]], failures: List[Dict[str, str]]) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Constructs the text and Block Kit for the Slack notification message.
    """
    lines = []
    if successes:
        lines.append(f"✅ *{len(successes)} issues assigned successfully:*")
        lines.extend([f"• *{s['key']}* – {s['title']} (→ {s['assignee']})" for s in successes])
    if failures:
        if lines:
            lines.append("")  # Blank line for separation
        lines.append(f"❌ *{len(failures)} issues failed:*")
        for f in failures:
            reason = f.get("error", "Unknown error")
            lines.append(f"• *{f['key']}* – {reason}")

    text = "\n".join(lines) if lines else "No issues were processed."

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    return text, blocks

# ------------- Lambda entry -------------

def lambda_handler(event=None, context=None):
    """
    Main handler for the AWS Lambda function.
    """
    # Use event payload values if provided, otherwise fall back to hardcoded defaults.
    event_data = event or {}
    issue_ids = event_data.get("issue_ids", ISSUE_IDS)
    assignee_id = event_data.get("assignee_id", ASSIGNEE_ID)

    # Initialize result tracking lists
    results = []     # Raw API responses for debugging
    successes = []   # Successfully assigned issues
    failures = []    # Failed assignments with error details

    # Process each issue identifier in the batch
    for issue_key in issue_ids:
        internal_id = fetch_issue_internal_id(issue_key)
        if not internal_id:
            msg = "Could not find issue or resolve its internal ID"
            results.append({"issue": issue_key, "error": msg})
            failures.append({"key": issue_key, "error": msg})
            continue

        # Attempt to assign the issue to the specified user
        result = assign_issue(internal_id, assignee_id)
        results.append({"issue": issue_key, "result": result})

        # Parse the GraphQL response to determine success/failure
        try:
            upd = result.get("data", {}).get("issueUpdate")
            if upd and upd.get("success"):
                issue = upd.get("issue", {})
                successes.append({
                    "key": issue.get("identifier", issue_key),
                    "title": issue.get("title", "Untitled"),
                    "assignee": issue.get("assignee", {}).get("name", "Unknown Assignee")
                })
            else:
                error_msg = "Linear API returned success=false"
                if result.get("errors"):
                    error_msg = f"GraphQL error: {result['errors'][0]['message']}"
                failures.append({"key": issue_key, "error": error_msg})
        except Exception as e:
            failures.append({"key": issue_key, "error": f"Error parsing API response: {e}"})

    # Always attempt to send a Slack notification with the results.
    slack_status = None
    try:
        if successes or failures:
            text, blocks = build_slack_message(successes, failures)
            slack_status = send_slack_message(SLACK_CHANNEL_ID, text=text, blocks=blocks)
        else:
            slack_status = {"ok": True, "message": "No issues to process."}
    except Exception as e:
        print(f"Failed to send Slack message: {e}")
        slack_status = {"ok": False, "error": str(e)}

    # Construct the final response body for the Lambda invocation.
    body = {
        "linear_results": results,
        "slack_status": slack_status,
        "summary": {
            "success_count": len(successes),
            "failure_count": len(failures)
        }
    }

    return {
        "statusCode": 200,
        "body": json.dumps(body, indent=2)
    }
