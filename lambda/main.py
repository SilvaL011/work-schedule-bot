import os, json, logging
import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

SECRET_NAME = os.environ.get("SECRET_NAME", "work-schedule-bot")

def _load_secret():
    """
    Read JSON config from AWS Secrets Manager and return it as a dict.
    Expects one JSON object (the one you saved earlier).
    """
    region = os.getenv("AWS_REGION", "ca-central-1")  # use your region; CLI sets this when you run in Lambda
    try:
        client = boto3.session.Session().client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=SECRET_NAME)
    except ClientError as e:
        log.error(f"Failed to read secret {SECRET_NAME} in {region}: {e}")
        raise

    # SecretString holds the plaintext JSON you stored
    if "SecretString" in resp and resp["SecretString"]:
        return json.loads(resp["SecretString"])
    # (rare) if stored as binary
    if "SecretBinary" in resp and resp["SecretBinary"]:
        return json.loads(resp["SecretBinary"].decode("utf-8"))
    raise RuntimeError("Secret had no SecretString or SecretBinary")
