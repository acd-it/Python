import requests
import json
import subprocess
import time


## ACD - 
## JIRA Mass User Lifecycle Scripts Script ##

# This script retrieves a JIRA Admin API key from a password manager, reads a list of user account IDs
# from a file called ids.txt that is in the same directory as the script, and disables each account using the Atlassian API. 
# It provides progress updates and error messages, and summarizes the total number of accounts successfully disabled.
# The 'ids.txt' file must have one account ID per line to work.




## REF: https://developer.atlassian.com/cloud/admin/user-management/rest/api-group-lifecycle/#api-group-lifecycle ##
## REF: https://support.atlassian.com/organization-administration/docs/manage-an-organization-with-the-admin-apis/##



# Function to retrieve a secret key from a password manager using a command-line tool
def get_secret_key():
    try:
        result = subprocess.run(
            [""],## Fetch secret from secrets manager
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()  # Return the secret key after stripping whitespace
    except subprocess.CalledProcessError as e:
        print(f"Failed to retrieve secret key: {e}")  # Handle errors if the command fails
        exit(1)

# Function to disable a user account using the Atlassian API
def disable_account(account_id, secret_key):
    # Construct the API endpoint URL for account disablement
    url = f"https://api.atlassian.com/users/{account_id}/manage/lifecycle/disable"
    headers = {
        "Content-Type": "application/json",  # Set content type for JSON
        "Authorization": f"Bearer {secret_key}"  # Use the secret key for authorization
    }
    payload = {
        "message": "Former employee"  # Message included in the payload for context
    }
    try:
        # Make a POST request to disable the account
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            verify=True
        )
        response.raise_for_status()  # Raise an error for HTTP errors
        print(f"Successfully disabled account: {account_id}")  # Success message
        return True
    except requests.RequestException as e:
        # Handle request errors
        print(f"Failed to disable account {account_id}: {e}")
        print("Response status code:", e.response.status_code)  # Print the response status code
        print("Response content:", e.response.content.decode())  # Print the response content
        return False

# Main function to coordinate the account disabling process
def main():
    secret_key = get_secret_key()  # Retrieve the secret key
    
    # Read account IDs from a text file, ensuring to strip whitespace
    with open('ids.txt', 'r') as file:
        account_ids = [line.strip() for line in file if line.strip()]
    
    total_accounts = len(account_ids)  # Total number of accounts to process
    disabled_accounts = 0  # Counter for successfully disabled accounts
    
    # Loop through each account ID and attempt to disable it
    for i, account_id in enumerate(account_ids, 1):
        print(f"Processing account {i} of {total_accounts}: {account_id}")  # Progress update
        if disable_account(account_id, secret_key):  # Attempt to disable the account
            disabled_accounts += 1  # Increment counter if successful
        time.sleep(1)  # Add a 1-second delay to avoid rate limiting
    
    # Final summary of the process
    print(f"\nProcess completed. Disabled {disabled_accounts} out of {total_accounts} accounts.")

# Entry point of the script
if __name__ == "__main__":
    main()
