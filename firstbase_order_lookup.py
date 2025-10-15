#!/usr/bin/env python3
## ACD - Firstbase Hardware Order Lookup Utility ##
##
## DESCRIPTION:
##   Interactive command-line tool to look up hardware orders from Firstbase by user
##   email address. The script resolves the user's Firstbase person ID via the SCIM API,
##   retrieves all associated hardware orders, and displays a simplified summary with
##   direct links to view each order in the Firstbase web interface.
##
## USAGE:
##   python firstbase_order_status.py
##
##   The script will prompt for an email address, then display:
##   - User's full name
##   - Order count
##   - For each order: status and direct link to Firstbase UI
##
## REQUIREMENTS:
##   - Python 3.7+
##   - requests library (pip install requests)
##   - (Optional) 1Password CLI (op) for secret management
##
## AUTHENTICATION OPTIONS (choose one):
##   Option 1: Set environment variable FIRSTBASE_API_KEY with your API key
##   Option 2: Set FIRSTBASE_OP_SECRET_PATH to a 1Password secret reference
##             (requires `op` CLI and active session: run `op signin` first)
##
##   Example: export FIRSTBASE_API_KEY="your-api-key-here"
##   Example: export FIRSTBASE_OP_SECRET_PATH="op://vault/item/field"
##
## OUTPUT:
##   Displays order information including:
##   - Order status
##   - Direct links to orders in Firstbase UI (https://app.firstbasehq.com/logistics/ORDER_ID)
##
## NOTES:
##   - All API credentials must be provided via environment variables or 1Password
##   - The script uses Firstbase SCIM API for user lookup and REST API for orders
##   - Logging is configured to INFO level for operational visibility
##
################################################################################

import os
import requests
import subprocess
import json
import sys
import logging
import urllib.parse

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_secret(path):
    """Retrieves secrets from 1Password CLI"""
    result = subprocess.run(['op', 'read', path], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "1Password CLI returned a non-zero exit code")
    return result.stdout.strip()


def load_api_key() -> str:
    """Load the Firstbase API key from environment variables or 1Password."""
    api_key = os.environ.get("FIRSTBASE_API_KEY")
    if api_key:
        return api_key.strip()

    secret_path = os.environ.get("FIRSTBASE_OP_SECRET_PATH")
    if secret_path:
        return get_secret(secret_path).strip()

    raise RuntimeError(
        "Missing API credentials. Set FIRSTBASE_API_KEY or FIRSTBASE_OP_SECRET_PATH before running."
    )

# Define base URLs for Firstbase API endpoints and web UI
SCIM_BASE_URL = "https://apipub.firstbasehq.com/scim/v2"  # SCIM API for user lookups
API_BASE_URL = "https://apipub.firstbasehq.com/api"        # REST API for orders
FIRSTBASE_UI_URL = "https://app.firstbasehq.com/logistics" # Web interface base URL

def get_person_id_by_email(email, api_key):
    """
    Query the Firstbase SCIM API to get a person's ID by their email address.
    """
    encoded_email = urllib.parse.quote(email)
    url = f"{SCIM_BASE_URL}/Users?filter=emails.value%20eq%20%22{encoded_email}%22"
    headers = {
        "accept": "application/scim+json",
        "Authorization": f"ApiKey {api_key}"
    }
    
    try:
        logger.info(f"Looking up person ID for email: {email}")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if data.get("Resources") and len(data["Resources"]) > 0:
            person_id = data["Resources"][0].get("id")
            name = data["Resources"][0].get("name", {})
            full_name = f"{name.get('givenName', '')} {name.get('familyName', '')}".strip()
            logger.info(f"Found person ID: {person_id} for {full_name}")
            return person_id, full_name
        else:
            logger.warning(f"No person found with email: {email}")
            return None, None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying person by email: {e}")
        return None, None
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON response: {e}")
        return None, None

def get_orders_by_person_id(person_id, api_key):
    """
    Query the Firstbase API to get all orders for a specific person.
    """
    url = f"{API_BASE_URL}/orders?page=1&size=50&personId={person_id}"
    headers = {
        "accept": "application/json",
        "Authorization": f"ApiKey {api_key}"
    }
    
    try:
        logger.info(f"Looking up orders for person ID: {person_id}")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if data.get("data") and len(data["data"]) > 0:
            logger.info(f"Found {len(data['data'])} orders")
            return data["data"]
        else:
            logger.warning(f"No orders found for person ID: {person_id}")
            return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Error querying orders by person ID: {e}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON response: {e}")
        return []

def format_simplified_order(order, order_num):
    """
    Format order information into a simplified, readable output with a UI link.
    """
    order_id = order.get("id", "Unknown")
    status = order.get("status", "Unknown")
    created_at = order.get("createdAt", "N/A")
    
    # Create a direct link to the order in the Firstbase UI
    order_link = f"{FIRSTBASE_UI_URL}/{order_id}"
    
    output = f"Order {order_num}: Status: {status} | Link: {order_link}"
    return output

def main():
    """Main function to run the script"""
    # Load API credentials from environment or 1Password
    try:
        api_key = load_api_key()
    except Exception as e:
        logger.error(f"Error retrieving API key: {e}")
        print("Failed to retrieve API key. Set FIRSTBASE_API_KEY or configure FIRSTBASE_OP_SECRET_PATH (requires 'op signin').")
        sys.exit(1)

    # Prompt user for the email address to look up
    email = input("Enter the email address to lookup: ").strip()

    if not email:
        print("No email provided. Exiting.")
        sys.exit(1)

    # Look up the person ID in Firstbase using SCIM API
    person_id, full_name = get_person_id_by_email(email, api_key)
    
    if not person_id:
        print(f"No user found with email: {email}")
        sys.exit(1)
    
    print(f"\nFound user: {full_name}")

    # Retrieve all hardware orders associated with this person
    orders = get_orders_by_person_id(person_id, api_key)
    
    if not orders:
        print(f"No orders found for {email}")
        sys.exit(0)
    
    print(f"\nFound {len(orders)} order(s):")
    
    # Process each order with simplified output
    for i, order in enumerate(orders, 1):
        if order.get("id"):
            simplified_order = format_simplified_order(order, i)
            print(simplified_order)
        else:
            print(f"Order {i}: Could not retrieve order information")

if __name__ == "__main__":
    main()
