import os
import json
import logging
import boto3
from botocore.exceptions import ClientError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

log = logging.getLogger(__name__)

SECRET_NAME = os.environ.get("SECRET_NAME", "work-schedule-bot")

def _load_secret():
    #Read JSON config from AWS Secrets Manager and return it as a dict.
    #Expects one JSON object (the one you saved earlier).

    region = os.getenv("AWS_REGION", "ca-central-1")  # use your region; CLI sets this when you run in Lambda
    try:
        client = boto3.session.Session().client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=SECRET_NAME)
    except ClientError as e:
        log.error(f"Failed to read secret {SECRET_NAME} in {region}: {e}")
        raise

    if "SecretString" in resp and resp["SecretString"]:
        return json.loads(resp["SecretString"])
    if "SecretBinary" in resp and resp["SecretBinary"]:
        return json.loads(resp["SecretBinary"].decode("utf-8"))
    raise RuntimeError("Secret had no SecretString or SecretBinary")

def _google_creds(secret: dict) -> Credentials:
    #Turn the refresh_token + client id/secret into OAuth credentials.
    #google-auth will auto-exchange the refresh token for a short-lived access token.

    return Credentials(
        token=None,
        refresh_token=secret["refresh_token"],
        token_uri=secret["token_uri"],
        client_id=secret["client_id"],
        client_secret=secret["client_secret"],
        scopes=[
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar",
        ],
    )

def _build_google_clients(creds):
    #Create service objects for Gmail and Calendar.

    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    gcal  = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return gmail, gcal

def handler(event, context):
    """
    EventBridge will call this function on a schedule.
    For now, we only prove we can load the secret and build Google clients.
    """
    cfg = _load_secret()
    creds = _google_creds(cfg)
    gmail, gcal = _build_google_clients(creds)

    # Minimal return so you can see something in CloudWatch later
    return {
        "ok": True,
        "loaded_secret_keys": sorted(cfg.keys()),
        "gmail_client_built": bool(gmail is not None),
        "gcal_client_built": bool(gcal is not None),
    }

if __name__ == "__main__":
    # Lets you run:  python lambda/main.py
    os.environ.setdefault("AWS_REGION", "ca-central-1")
    cfg = _load_secret()
    print("Loaded secret keys:", sorted(cfg.keys()))
    creds = _google_creds(cfg)
    gmail, gcal = _build_google_clients(creds)
    print("Google client objects built OK")