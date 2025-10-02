#Run locally once to generate a Google refresh token (for Gmail read + Calendar write). 
# Will paste the printed JSON into AWS Secrets Manager.

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]

def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        "scripts/client_secret.json", SCOPES
    )
    try:
        creds = flow.run_local_server(port=0, prompt="consent", authorization_prompt_message="")
    except Exception:
        print("\n[Info] Local browser auth failed. Falling back to copy/paste mode.\n")
        creds = flow.run_console()

    print("\nCopy these values into AWS Secrets Manager (JSON):\n")
    print({
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "calendar_id": "primary",   # change if you use a specific calendar
        "sender_filter": "",         # e.g., schedules@your-employer.com
        "timezone": "America/Toronto",
    })

if __name__ == "__main__":
    main()
