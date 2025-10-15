#!/usr/bin/env python3
## ACD - Calendly User Provisioning Lambda ##
##
## DESCRIPTION:
##   AWS Lambda function that handles automated user provisioning and deprovisioning
##   for Calendly. Receives webhook events from an upstream access management platform,
##   validates the request using a shared secret, and either invites new users to or
##   removes existing users from the Calendly organization.
##
## USAGE:
##   Deploy as an AWS Lambda function behind an API Gateway. Configure webhook in
##   your access management platform to POST events to the Lambda endpoint.
##
##   Webhook payload formats:
##   - Provision user:   {"target_user": {"email": "user@example.com"}}
##   - Deprovision user: {"user": {"email": "user@example.com"}}
##
## REQUIREMENTS:
##   - Python 3.8+
##   - requests library
##
## REQUIRED ENVIRONMENT VARIABLES:
##   PROVISIONING_WEBHOOK_SECRET - Shared secret for webhook authentication
##   CALENDLY_API_TOKEN          - Calendly API token with admin permissions
##   CALENDLY_ORG_UUID           - Calendly organization UUID
##
## AUTHENTICATION:
##   Requests must include header: Authorization: Bearer <PROVISIONING_WEBHOOK_SECRET>
##
## RETURN VALUES:
##   - 401: Unauthorized (invalid/missing secret)
##   - 400: Bad request (missing email or invalid payload)
##   - 200: Success with JSON response indicating action taken
##
## NOTES:
##   - All secrets must be provided via Lambda environment variables
##   - Deprovisioning looks up the user's membership by email before removal
##   - Replace placeholder environment variable values before deployment
##
################################################################################

import os
import json
import requests

# Load required configuration from environment variables
WEBHOOK_SHARED_SECRET = os.environ["PROVISIONING_WEBHOOK_SECRET"]
CALENDLY_API_TOKEN = os.environ["CALENDLY_API_TOKEN"]
CALENDLY_ORG_UUID = os.environ["CALENDLY_ORG_UUID"]

def unauthorized():
    return {
        "statusCode": 401,
        "body": json.dumps({"error": "Unauthorized"})
    }

def provision_user(email: str):
    """Send invitation to add user to Calendly organization."""
    url = f"https://api.calendly.com/organizations/{CALENDLY_ORG_UUID}/invitations"
    payload = json.dumps({"email": email})
    headers = {
        "Authorization": f"Bearer {CALENDLY_API_TOKEN}",
        "Content-Type": "application/json"
    }
    # POST to invitations endpoint to send invite email
    resp = requests.post(url, headers=headers, data=payload)
    # Accept any 2xx status code as success
    return resp.status_code in [200, 201, 202]

def deprovision_user(email: str):
    """Remove user from Calendly organization by looking up their membership and deleting it."""
    # First, look up the user's organization membership by email
    lookup_url = (
        f"https://api.calendly.com/organization_memberships"
        f"?email={email}&organization=https://api.calendly.com/organizations/{CALENDLY_ORG_UUID}"
    )
    headers = {
        "Authorization": f"Bearer {CALENDLY_API_TOKEN}",
        "Content-Type": "application/json"
    }
    lookup = requests.get(lookup_url, headers=headers)
    if lookup.status_code != 200:
        return False

    # Extract membership records from API response
    data = lookup.json().get("collection", [])
    if not data:
        return False  # User not found in organization

    # Extract the membership UUID from the first matching record
    membership_uuid = data[0].get("uri", "").split("/")[-1]
    if not membership_uuid:
        return False

    # Delete the membership to remove user from organization
    delete_url = f"https://api.calendly.com/organization_memberships/{membership_uuid}"
    delete = requests.delete(delete_url, headers=headers)
    return delete.status_code in [200, 204]

def lambda_handler(event, context):
    """Main Lambda handler for processing provisioning webhook events."""
    # Verify the webhook request is authenticated with the correct shared secret
    auth = event.get("headers", {}).get("Authorization", "")
    if auth != f"Bearer {WEBHOOK_SHARED_SECRET}":
        return unauthorized()

    # Parse the webhook payload
    try:
        payload = json.loads(event.get("body", "{}"))
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"response": "NO_PROVISIONING_ACTION"})}

    # Handle deprovision event (user removal)
    if "user" in payload:
        email = payload["user"].get("email")
        if not email:
            return {"statusCode": 400, "body": json.dumps({"response": "NO_PROVISIONING_ACTION"})}
        success = deprovision_user(email)
        return {
            "statusCode": 200,
            "body": json.dumps({
                "response": "DEPROVISIONED_USER" if success else "NO_PROVISIONING_ACTION"
            })
        }

    # Handle provision event (user invitation)
    elif "target_user" in payload:
        email = payload["target_user"].get("email")
        if not email:
            return {"statusCode": 400, "body": json.dumps({"response": "NO_PROVISIONING_ACTION"})}
        success = provision_user(email)
        return {
            "statusCode": 200,
            "body": json.dumps({
                "response": "PROVISIONED_USER" if success else "NO_PROVISIONING_ACTION"
            })
        }

    else:
        return {"statusCode": 400, "body": json.dumps({"response": "NO_PROVISIONING_ACTION"})}
