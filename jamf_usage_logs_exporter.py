## ACD - Jamf Pro Computer Usage Logs Exporter ##
##
## DESCRIPTION:
##   This script exports device usage history from Jamf Pro to a CSV file. It fetches
##   the list of all computers, retrieves usage logs for each device, filters the logs
##   to a specified rolling time window, and writes the results to a CSV file with
##   device name, event type, username, and timestamp information.
##
## USAGE:
##   python fetch_computer_usage_logs.py
##
## REQUIREMENTS:
##   - Python 3.7+
##   - requests library (pip install requests)
##
## REQUIRED ENVIRONMENT VARIABLES:
##   JAMF_BASE_URL      - Jamf Pro instance base URL (e.g., "https://company.jamfcloud.com")
##   JAMF_BEARER_TOKEN  - Bearer token with read access to computer and history endpoints
##
## OPTIONAL ENVIRONMENT VARIABLES:
##   USAGE_WINDOW_DAYS  - Number of days to look back for usage logs (default: 30)
##
## OUTPUT:
##   Creates a CSV file named "computer_usage_logs.csv" with columns:
##   - Device Name
##   - Event
##   - Username
##   - Date & Time
##
## NOTES:
##   - The script includes retry logic with 3 attempts per API call
##   - Logs are filtered to the specified rolling window and sorted by timestamp
##   - Real-time logging with timestamps for monitoring progress
##   - All credentials must be provided via environment variables
##
################################################################################

import os
import requests
import csv
import sys
import time
from datetime import datetime, timedelta

# Logging Function
def log(message):
    """Prints messages with a timestamp in real-time."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}")
    sys.stdout.flush()  # Ensure logs appear immediately

# Configure API headers with bearer token authentication
headers = {
    'accept': 'application/json',
    'Authorization': f'Bearer {os.environ["JAMF_BEARER_TOKEN"]}',
}

# Construct base URLs for Jamf Pro API endpoints
jamf_base_url = os.environ["JAMF_BASE_URL"].rstrip("/")
base_history_url = f"{jamf_base_url}/JSSResource/computerhistory/id/"
computers_url = f"{jamf_base_url}/JSSResource/computers"

# Define the rolling time window for log filtering
window_days = int(os.getenv("USAGE_WINDOW_DAYS", "30"))
end_date = datetime.utcnow()
start_date = end_date - timedelta(days=window_days)

# Function to handle retries
def make_api_call(url, retries=3, delay=5):
    """Attempts to make an API call with retries."""
    for attempt in range(retries):
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response
        else:
            log(f"❌ API call failed with status {response.status_code}, attempt {attempt + 1} of {retries}. Retrying in {delay} seconds...")
            time.sleep(delay)
    log(f"❌ Failed after {retries} retries.")
    return None

# Step 1: Get list of all computers
log("Fetching list of computers from API...")
response_computers = make_api_call(computers_url)
if not response_computers:
    log("❌ Error fetching computers. Exiting.")
    sys.exit(1)

data_computers = response_computers.json()
computers_list = data_computers.get("computers", [])
if not computers_list:
    log("No computers found. Exiting.")
    sys.exit(0)

log(f"Retrieved {len(computers_list)} computers.")

# CSV File Name
csv_filename = "computer_usage_logs.csv"
log("Opening CSV file for writing...")
with open(csv_filename, mode="w", newline="") as file:
    writer = csv.writer(file)
    
    # Write header (only once)
    writer.writerow(["Device Name", "Event", "Username", "Date & Time"])
    
    # For date conversion (these were in your original script, only relevant if
    # your date strings contain "Today" or "Yesterday")
    today = end_date.strftime("%Y/%m/%d")
    yesterday = (end_date - timedelta(days=1)).strftime("%Y/%m/%d")
    
    def convert_date(date_str):
        """Convert 'Today' and 'Yesterday' to actual dates in your output string."""
        if "Today" in date_str:
            log(f"Replacing 'Today' in '{date_str}' with {today}")
            return date_str.replace("Today", today)
        elif "Yesterday" in date_str:
            log(f"Replacing 'Yesterday' in '{date_str}' with {yesterday}")
            return date_str.replace("Yesterday", yesterday)
        return date_str  # Return unchanged if no match

    # Step 2: Process each computer's history logs
    for computer in computers_list:
        computer_id = computer.get("id")
        log(f"Processing computer ID: {computer_id}")
        
        history_url = f"{base_history_url}{computer_id}"
        
        # Fetch Computer Usage Logs
        usage_logs_url = f"{history_url}/subset/computer_usage_logs"
        response_logs = make_api_call(usage_logs_url)
        if not response_logs:
            log(f"❌ Skipping computer ID {computer_id} due to failed logs fetch.")
            continue
        
        data_logs = response_logs.json()
        logs_list = data_logs.get("computer_history", {}).get("computer_usage_logs", [])
        if not logs_list:
            log(f"No usage logs found for computer ID {computer_id}.")
            continue
        
        log(f"Retrieved {len(logs_list)} log entries for computer ID {computer_id}.")

        # Filter out logs outside the desired date range
        filtered_logs = []
        for log_entry in logs_list:
            epoch_value = log_entry.get("date_time_epoch", 0)
            # If your epoch is in seconds, remove "/ 1000.0"
            log_datetime = datetime.utcfromtimestamp(epoch_value / 1000.0)
            
            if start_date <= log_datetime <= end_date:
                filtered_logs.append(log_entry)
        
        # Sort the filtered logs by epoch
        filtered_logs.sort(key=lambda x: x.get("date_time_epoch", 0))

        # If no logs remain after filtering, skip
        if not filtered_logs:
            log(f"No logs in the last {window_days} days for computer ID {computer_id}.")
            continue

        # Fetch Device Name (General subset)
        general_url = f"{history_url}/subset/General"
        response_device = make_api_call(general_url)
        if response_device:
            data_device = response_device.json()
            device_name = data_device.get("computer_history", {}).get("general", {}).get("name", f"Device {computer_id}")
        else:
            log(f"❌ Failed to fetch device name for computer ID {computer_id}. Using default name.")
            device_name = f"Device {computer_id}"
        
        log(f"Device Name for computer ID {computer_id}: {device_name}")
        
        # Write each filtered log entry to the CSV
        for log_entry in filtered_logs:
            # Keep your original textual date/time for CSV output
            # but do "Today"/"Yesterday" conversion if needed
            formatted_date = convert_date(log_entry.get("date_time", ""))
            writer.writerow([
                device_name,
                log_entry.get("event", ""),
                log_entry.get("username", ""),
                formatted_date
            ])
        
        log(f"Appended {len(filtered_logs)} log entries for computer ID {computer_id} to CSV.")

log(f"✅ CSV file successfully saved as: {csv_filename}")
