# ACD - CloudFlare RADAR URL Scan Script 
# This script asks the user for a web address, feeds the input to CloudFlare's RADAR scanner, 
# and then returns the scan results.

import os
import requests
import time
from datetime import datetime, timedelta, timezone

# Cloudflare account details (Referencing secret manager)
api_token = os.getenv('CLOUDFLARE_API_TOKEN')  # Store your API token securely
account_id = os.getenv('CLOUDFLARE_ACCOUNT_ID')  # Store your account ID securely

headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {api_token}'
}


def check_url_scan_status(input_url):
    """Checks for the most recent scan of a given URL within the last month."""
    check_url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan?page_hostname={input_url}'
    response = requests.get(check_url, headers=headers)
    
    if response.status_code == 200 and response.json().get('success'):
        tasks = response.json().get('result', {}).get('tasks', [])
        if tasks:
            now = datetime.utcnow().replace(tzinfo=timezone.utc)
            one_month_ago = now - timedelta(days=30)
            for task in sorted(tasks, key=lambda x: x['time'], reverse=True):
                task_time = datetime.fromisoformat(task['time'].rstrip('Z')).replace(tzinfo=timezone.utc)
                if task_time > one_month_ago:
                    return task['id']
    return None


def start_scan(input_url):
    """Starts a new scan for the given URL or returns recent scan ID if it exists."""
    scan_url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan'
    data = {'url': input_url}
    response = requests.post(scan_url, headers=headers, json=data)

    if response.status_code == 200:
        return response.json().get('result', {}).get('id'), True
    elif response.status_code == 409:
        # A recent scan already exists, fetch its ID
        return check_url_scan_status(input_url), False
    else:
        print(f"Failed to initiate scan. Status code: {response.status_code}")
        return None, False


def get_scan_results(scan_id):
    """Retrieves and formats the scan results."""
    results_url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan/{scan_id}'
    response = requests.get(results_url, headers=headers)
    if response.status_code == 200:
        data = response.json()
        scan_data = data.get('result', {}).get('scan', {})
        task = scan_data.get('task', {})
        verdicts = task.get('verdicts', {}).get('overall', {})

        formatted_results = (
            f"Scan ID: {task.get('uuid')}\n"
            f"URL: {task.get('url')}\n"
            f"Status: {task.get('status')}\n"
            f"Scan Start Time: {task.get('time')}\n"
            f"Scan End Time: {task.get('timeEnd')}\n"
            f"Visibility: {task.get('visibility')}\n"
            f"Client Location: {task.get('clientLocation')}\n"
            f"Scanned From: {task.get('scannedFrom', {}).get('colo')}\n"
            f"Effective URL: {task.get('effectiveUrl')}\n"
            f"Malicious: {verdicts.get('malicious')}\n"
            f"Categories: {', '.join(verdicts.get('categories', []))}\n"
            f"Google Safe Browsing Threat Types: {', '.join(verdicts.get('gsb_threat_types', []))}\n"
            f"Phishing: {', '.join(verdicts.get('phishing', []))}\n"
        )
        return formatted_results
    else:
        print("Failed to retrieve scan results.")
        return None


def main_workflow():
    # Step 1: Ask the user for the input URL
    input_url = input("Please enter the URL to scan: ")
    
    # Assuming response_url is known or fetched from your environment
    response_url = os.getenv('SLACK_RESPONSE_URL')  # Fetch Slack response URL from environment
    user_id = os.getenv('USER_ID')  # Fetch user ID from environment or context

    # Step 2: Check if a recent scan exists or start a new scan
    scan_id, started_new_scan = start_scan(input_url)
    if scan_id:
        # Wait a bit for the scan to complete if a new scan was started
        if started_new_scan:
            print("Waiting for scan to complete...")
            time.sleep(30)  # Adjust based on expected scan completion time

        # Step 3: Get the scan results
        formatted_results = get_scan_results(scan_id)
        if formatted_results:
            # Prepare the message payload with the screenshot URL
            screenshot_url = f'https://api.cloudflare.com/client/v4/accounts/{account_id}/urlscanner/scan/{scan_id}/screenshot'
            
            message_payload = {
                "text": formatted_results,
                "attachments": [
                    {
                        "fallback": "Required plain-text summary of the attachment.",
                        "color": "#36a64f",
                        "title": "Scan Screenshot",
                        "image_url": screenshot_url
                    }
                ]
            }
            # Step 4: Post the formatted data to the Slack webhook URL
            post_response = requests.post(response_url, json=message_payload)
            if post_response.status_code == 200:
                print("Information successfully posted to Slack.")
            else:
                print("Failed to post the information to Slack.")
        else:
            # If failed to retrieve scan results, post an error message to Slack
            requests.post(response_url, json={"text": "Failed to retrieve scan results. Please try again."})
    else:
        # If failed to initiate or find a scan, post an error message to Slack
        requests.post(response_url, json={"text": "Failed to initiate or find a scan for the URL. Please check the URL and try again."})


if __name__ == "__main__":
    main_workflow()
