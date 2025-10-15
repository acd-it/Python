## ACD - Okta to Slack User Group Synchronizer ##
##
## DESCRIPTION:
##   This script synchronizes users from Okta to a Slack user group. It fetches users
##   from Okta based on a configurable search query, maps their email addresses to Slack
##   user IDs, and updates a specified Slack user group with the matched users. Optional
##   static Slack user IDs can also be included in the group.
##
## USAGE:
##   python aes.py
##
## REQUIREMENTS:
##   - Python 3.7+
##   - requests library (pip install requests)
##
## REQUIRED ENVIRONMENT VARIABLES:
##   SLACK_TOKEN          - Slack bot token with usergroups:write and users:read.email scopes
##   OKTA_API_KEY         - Okta API key with read access to users
##   OKTA_DOMAIN          - Okta domain (e.g., "company.okta.com")
##
## OPTIONAL ENVIRONMENT VARIABLES:
##   OKTA_QUERY           - Okta search query filter (default: "REPLACE_WITH_OKTA_FILTER")
##   SLACK_USERGROUP_ID   - Target Slack user group ID (default: "REPLACE_WITH_SLACK_USERGROUP_ID")
##   STATIC_SLACK_USER_IDS - Comma-separated list of Slack user IDs to always include
##
## NOTES:
##   - All secrets must be provided via environment variables
##   - The script handles Okta pagination automatically
##   - Rate limiting for Slack API is handled with exponential backoff
##   - Replace placeholder values before production use
##
################################################################################
import json
import logging
import os
import sys
import time
from typing import Dict, Iterable, List, Optional, Tuple
import requests


def _load_static_slack_ids(raw_ids: Optional[str]) -> List[str]:
    """
    Parse a comma-separated string of Slack user IDs into a list.

    Returns an empty list when no static members are required.
    """
    if not raw_ids:
        return []
    return [user_id.strip() for user_id in raw_ids.split(",") if user_id.strip()]


# Configure lookups with environment-driven fallbacks so secrets never live in code.
OKTA_QUERY = os.getenv("OKTA_QUERY", "REPLACE_WITH_OKTA_FILTER")
SLACK_USERGROUP_ID = os.getenv("SLACK_USERGROUP_ID", "REPLACE_WITH_SLACK_USERGROUP_ID")
STATIC_SLACK_USER_IDS: List[str] = _load_static_slack_ids(os.getenv("STATIC_SLACK_USER_IDS"))


def configure_logging() -> None:
    """Configure the global logging settings for the script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def fetch_okta_users(okta_domain: str, okta_api_key: str, query: str) -> List[Dict]:
    """
    Fetch users from Okta that match the provided search query.

    Handles Okta pagination via Link headers and returns a list of user objects.
    """
    logger = logging.getLogger(__name__)
    base_url = f"https://{okta_domain}/api/v1/users"
    # Create a shared session so headers and connection pooling are reused.
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"SSWS {okta_api_key}",
        }
    )

    users: List[Dict] = []
    url = base_url
    # Use Okta's search + pagination params; limit 200 keeps payloads manageable.
    params = {"search": query, "limit": 200}
    logger.info("Starting Okta user search with query '%s'", query)

    while url:
        try:
            response = session.get(url, params=params if url == base_url else None, timeout=30)
        except requests.RequestException as exc:
            logger.error("Okta request failed during fetch: %s", exc)
            return []

        if response.status_code >= 400:
            snippet = response.text[:300].replace("\n", " ")
            logger.error(
                "Okta API error while fetching users (status %s): %s",
                response.status_code,
                snippet,
            )
            return []

        try:
            page_users = response.json()
        except json.JSONDecodeError as exc:
            logger.error("Failed to decode Okta response JSON: %s", exc)
            return []

        if not isinstance(page_users, list):
            logger.error("Unexpected Okta response format; expected list, got %s", type(page_users))
            return []

        users.extend(page_users)
        # Log page-level progress to help trace pagination behavior.
        logger.info("Fetched %d users from Okta (total so far: %d)", len(page_users), len(users))

        next_url = _parse_link_header(response.headers.get("link"))
        if next_url:
            logger.debug("Following Okta pagination link to next page.")
        url = next_url
        params = None

    logger.info("Completed Okta user fetch; total users found: %d", len(users))
    return users


def _parse_link_header(link_header: Optional[str]) -> Optional[str]:
    """
    Parse the Okta Link header to find the URL for the next page, if present.
    """
    if not link_header:
        return None

    parts = link_header.split(",")
    for part in parts:
        section = part.strip().split(";")
        if len(section) < 2:
            continue
        url_part = section[0].strip()
        rel_part = section[1].strip()
        if rel_part == 'rel="next"' and url_part.startswith("<") and url_part.endswith(">"):
            return url_part[1:-1]
    return None


def map_okta_to_slack_ids(okta_users: Iterable[Dict], slack_token: str) -> Tuple[List[str], List[str]]:
    """
    Map Okta user emails to Slack user IDs.

    Returns a tuple of (Slack user IDs found, emails not found in Slack).
    """
    logger = logging.getLogger(__name__)
    if not isinstance(okta_users, list):
        okta_users = list(okta_users)

    # Provide high-level visibility into the mapping workload.
    logger.info("Starting Slack user mapping for %d Okta users", len(okta_users))

    # Use a Session to keep TCP connections alive across many lookups.
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {slack_token}",
            "Content-Type": "application/json;charset=utf-8",
        }
    )

    slack_user_ids: List[str] = []
    missing_emails: List[str] = []
    seen_ids = set()

    for index, user in enumerate(okta_users, start=1):
        email = _extract_email(user)
        if not email:
            # Some Okta users can be missing profile email/login fields.
            logger.debug("Skipping Okta user without primary email at index %d", index)
            continue

        logger.debug("Looking up Slack user by email: %s", email)
        slack_user_id = _lookup_slack_user_id(session, email, logger)
        if slack_user_id:
            if slack_user_id not in seen_ids:
                slack_user_ids.append(slack_user_id)
                seen_ids.add(slack_user_id)
                logger.debug("Mapped email %s to Slack ID %s", email, slack_user_id)
        else:
            missing_emails.append(email)
            # Slack's lookup API returns users_not_found when the email doesn't exist.
            logger.info("Slack user not found for Okta email: %s", email)

    logger.info(
        "Slack mapping complete; mapped %d users, %d emails not found",
        len(slack_user_ids),
        len(missing_emails),
    )
    return slack_user_ids, missing_emails


def _extract_email(okta_user: Dict) -> Optional[str]:
    """Extract the primary email address from an Okta user record."""
    profile = okta_user.get("profile", {})
    email = profile.get("email") or profile.get("login")
    if isinstance(email, str):
        return email
    return None


def _lookup_slack_user_id(session: requests.Session, email: str, logger: logging.Logger) -> Optional[str]:
    """
    Look up a Slack user ID by email, handling rate limits and HTTP errors.
    """
    url = "https://slack.com/api/users.lookupByEmail"
    params = {"email": email}
    max_retries = 5
    backoff_seconds = 1.0  # Exponential backoff base window.

    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            logger.error("Slack lookup failed for %s on attempt %d: %s", email, attempt, exc)
            time.sleep(backoff_seconds)
            backoff_seconds *= 2
            continue

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", "1"))
            logger.warning(
                "Slack rate limit hit while looking up %s; retrying in %s seconds",
                email,
                retry_after,
            )
            time.sleep(retry_after)
            continue

        if response.status_code >= 400:
            snippet = response.text[:300].replace("\n", " ")
            logger.error(
                "Slack lookup HTTP error for %s (status %s): %s",
                email,
                response.status_code,
                snippet,
            )
            return None

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            logger.error("Failed to decode Slack lookup response for %s: %s", email, exc)
            return None

        if payload.get("ok"):
            user_info = payload.get("user", {})
            slack_id = user_info.get("id")
            if isinstance(slack_id, str):
                return slack_id
            logger.error("Slack lookup succeeded but ID missing for %s", email)
            return None

        error_code = payload.get("error", "unknown_error")
        if error_code == "ratelimited":
            logger.warning("Slack API ratelimited for %s; backing off for %s seconds", email, backoff_seconds)
            time.sleep(backoff_seconds)
            backoff_seconds *= 2
            continue

        if error_code == "users_not_found":
            logger.debug("Slack API reported user not found for %s", email)
            return None

        logger.error("Slack lookup failed for %s with error: %s", email, error_code)
        return None

    logger.error("Exceeded max retries while looking up Slack user for %s", email)
    return None


def update_slack_usergroup(slack_token: str, usergroup_id: str, slack_user_ids: List[str]) -> bool:
    """
    Update the Slack user group with the provided Slack user IDs.
    """
    logger = logging.getLogger(__name__)
    url = "https://slack.com/api/usergroups.users.update"
    # session.post ensures consistent headers and connection reuse.
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {slack_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
    )

    payload = {"usergroup": usergroup_id, "users": ",".join(slack_user_ids)}
    # Log the intent (but not secrets) so operators know what was attempted.
    logger.info("Updating Slack user group %s with %d users", usergroup_id, len(slack_user_ids))
    logger.debug("Slack update payload: %s", payload)

    try:
        response = session.post(url, data=payload, timeout=30)
    except requests.RequestException as exc:
        logger.error("Failed to call Slack user group update API: %s", exc)
        return False

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "n/a")
        logger.error(
            "Slack user group update hit rate limit (Retry-After: %s); aborting update",
            retry_after,
        )
        return False

    if response.status_code >= 400:
        snippet = response.text[:300].replace("\n", " ")
        logger.error(
            "Slack user group update HTTP error (status %s): %s",
            response.status_code,
            snippet,
        )
        return False

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        logger.error("Failed to decode Slack update response JSON: %s", exc)
        return False

    if payload.get("ok"):
        logger.info("Slack user group update succeeded.")
        return True

    error_code = payload.get("error", "unknown_error")
    logger.error("Slack user group update failed with error: %s", error_code)
    return False


def validate_env() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Validate that all required environment variables are set.
    """
    slack_token = os.getenv("SLACK_TOKEN")
    okta_api_key = os.getenv("OKTA_API_KEY")
    okta_domain = os.getenv("OKTA_DOMAIN")

    missing = [name for name, value in [("SLACK_TOKEN", slack_token), ("OKTA_API_KEY", okta_api_key), ("OKTA_DOMAIN", okta_domain)] if not value]
    if missing:
        logging.error("Missing required environment variable(s): %s", ", ".join(missing))
        return slack_token, okta_api_key, okta_domain

    logging.info(
        "All required environment variables present: %s",
        ", ".join(name for name in ["SLACK_TOKEN", "OKTA_API_KEY", "OKTA_DOMAIN"]),
    )
    return slack_token, okta_api_key, okta_domain


def main() -> int:
    """Main entry point for synchronizing the Slack user group."""
    configure_logging()
    logger = logging.getLogger(__name__)

    slack_token, okta_api_key, okta_domain = validate_env()
    if not all([slack_token, okta_api_key, okta_domain]):
        logger.error("Aborting due to missing environment variables.")
        return 1

    if OKTA_QUERY == "REPLACE_WITH_OKTA_FILTER":
        logger.warning("OKTA_QUERY placeholder is still set; adjust it to target the right Okta cohort.")
    if SLACK_USERGROUP_ID == "REPLACE_WITH_SLACK_USERGROUP_ID":
        logger.warning("SLACK_USERGROUP_ID placeholder is still set; update before running in production.")
    if not STATIC_SLACK_USER_IDS:
        logger.info("No STATIC_SLACK_USER_IDS configured; only dynamic matches will be applied.")

    okta_users = fetch_okta_users(okta_domain, okta_api_key, OKTA_QUERY)
    if not okta_users:
        logger.error("No Okta users retrieved; aborting Slack update.")
        return 1

    slack_user_ids, missing_emails = map_okta_to_slack_ids(okta_users, slack_token)
    if not slack_user_ids:
        logger.error("No Slack users mapped; aborting Slack user group update.")
        return 1

    # Merge dynamically mapped IDs with statically required ones while deduplicating.
    combined_user_ids = list({*slack_user_ids, *STATIC_SLACK_USER_IDS})
    logger.info(
        "Including %d static Slack user IDs in addition to mapped users",
        len(set(STATIC_SLACK_USER_IDS) - set(slack_user_ids)),
    )

    success = update_slack_usergroup(slack_token, SLACK_USERGROUP_ID, combined_user_ids)

    logger.info(
        "Summary: Okta users fetched=%d, Slack users mapped=%d, missing emails=%d, static additions=%d, update success=%s",
        len(okta_users),
        len(slack_user_ids),
        len(missing_emails),
        len(STATIC_SLACK_USER_IDS),
        success,
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
