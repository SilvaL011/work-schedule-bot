
# Work-Schedule-Bot

This project was built in order to automate the input of my work schedule(received by email) to my google calendar. It was a boring manual process that I would constantly forget to do, leading to complications later on the week. Overall, a simpler approach could've been used to solve this issue, but I saw it as a great opportunity to develop further some skills I don't get the chance to put into practice frequently.




## Authors

- [@SilvaL011](https://www.github.com/SilvaL011)


## Stack
- AWS: Lambda, EventBridge, Secrets Manager, CloudWatch
- Google: Gmail API, Calendar API
- Python: google-api-python-client, google-auth, beautifulsoup4, python-dateutil
- IaC: Terraform

## Skills Used

**Cloud & DevOps**
- AWS Lambda (serverless compute), EventBridge (scheduling), Secrets Manager (secure config), IAM (least-privilege), CloudWatch (logs)
- AWS CLI for one-off invokes and log tailing

**Infrastructure as Code**
- Terraform: provider lockfile, reproducible builds, clean state hygiene, per-env variables

**APIs & Auth**
- Google Gmail API + Calendar API
- OAuth 2.0 “installed app” flow with long-lived refresh token (stored in Secrets Manager)

**Python Engineering**
- Python 3.12, vendored dependencies for Lambda packaging
- Parsing with BeautifulSoup, datetime handling with `dateutil` + `zoneinfo`, regex for robust extraction

**Reliability / Design**
- Idempotent “upsert” events using a stable hash in `extendedProperties.private`
- Overlap detection to avoid duplicating manual calendar events
- Config via environment variables (title, color, subject filter, email lookback window)

**Security & Compliance**
- No secrets in git (`.gitignore` rules for client secrets/tokens/state)
- IAM policy scoped to a single secret ARN (principle of least privilege)

**Observability**
- Structured logging to CloudWatch, on-demand test invokes
- (Optional) CloudWatch alarm on Lambda errors

**Process & Collaboration**
- Git-based workflow, clear README/RUNBOOK
- Easy toggle between “test every minute” and “weekly cron” via Terraform vars

## Features
- Functioning Now
    - ✅ Gmail → parse Synerion table
    - ✅ Create/Update Google Calendar events (orange)
    - ✅ Idempotent: private event hash + overlap check
    - ✅ Weekly schedule via EventBridge

- Future Plan
    - Parser plug-ins for other templates
    - CloudWatch alarms / notifications
    - EventBridge Scheduler with timezone support
    - Unit tests for parser & CI



## Architecture

- Read from gmail  
- Lambda function parses latest email titled "Publish Schedule  Notification"  
    - Secrets in AWS Secrets Manager (Google client_id/secret, refresh_token, calendar_id, sender_filter, timezone)
- Create google calendar event  


## Environment Variables

Lambda variables
| Variable         | Default | Purpose                                    |
|------------------|---------|--------------------------------------------|
| SECRET_NAME      | work-schedule-bot | Secrets Manager name              |
| EVENT_COLOR_ID   | 6       | Google event color (orange-ish)            |
| SHIFT_TITLE      | Work    | Event title                                |
| SUBJECT_FILTER   | Publish Schedule Notification | Email subject filter |

Terraform variables
| Variable              | Example      | Purpose                                      |
|-----------------------|--------------|----------------------------------------------|
| region                | us-east-2    | AWS region (keep consistent with the secret) |
| secret_name           | work-schedule-bot | Secrets Manager name                      |
| schedule_expression   | cron(0 14 ? * SUN *) | Weekly trigger (UTC)                  |



## Deployment

### 0) Prerequisites
- Python 3.12+
- Terraform 1.6+
- AWS CLI v2 (and an AWS account you control)

### 1) AWS Setup

1) Create an IAM user for deploys
- Console → IAM → Users → Add users
- Input desired name
- Access type: Access key - Programmatic access
- Permissions (MVP): AdministratorAccess (you can restrict later)
- Save the Access Key ID and Secret Access Key

2) Configure the AWS CLI (use the same region this project expects)
```bash
aws configure
# AWS Access Key ID: <paste>
# AWS Secret Access Key: <paste>
# Default region name: us-east-2
# Default output format: json
```
- Why us-east-2? This repo defaults to Ohio for Lambda + Secrets Manager. You can change regions later—just be consistent everywhere

### 2) Google OAuth setup

1) Create a Google Cloud project
- [console.cloud.google.com](console.cloud.google.com)→ Select project → New Project → name it work-schedule-bot

2) Enable APIs
- APIs & Services → Enable APIs & Services → enable:  
    - Gmail API
    - Google Calendar API
- OAuth consent screen
    - APIs & Services → OAuth consent screen
    - User type: External
    - Add yourself (the Gmail account that receives the schedule) as a Test User
- Create OAuth client
    - APIs & Services → Credentials → Create Credentials → OAuth client ID
    - Application type: Desktop app
    - Download the JSON (client_secret_*.json).
You now have the client ID and client secret, but you still need a refresh token. We’ll generate it after cloning using the helper script in this repo.

3) Clone & prepare the repo
- git clone
- cd work-schedule-bot
- python3 -m venv .venv
- source .venv/bin/activate   
    - Windows PowerShell: .venv\Scripts\Activate.ps1
- Install the OAuth helper deps (only needed to get your refresh token once):
```bash
pip install google-auth google-auth-oauthlib google-api-python-client
```
- Put your downloaded client secret file at:
```bash
scripts/client_secret.json
```
- Generate your refresh token:
```bash
python scripts/get_refresh_token.py
```
- A browser window opens → log into the Gmail account that receives the schedule.
- On success, the script prints a JSON blob with: client_id, client_secret, refresh_token, token_uri.
- Copy that JSON (you’ll paste it into AWS Secrets Manager next)

4) Create the AWS Secret
We keep all Google creds + your bot config in AWS Secrets Manager as one JSON object.
- Open AWS → Secrets Manager (region us-east-2).
- Store a new secret → Other type of secret → Plaintext.
- Paste and substitute for relevant info:
```bash
{
  "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
  "client_secret": "YOUR_CLIENT_SECRET",
  "refresh_token": "YOUR_REFRESH_TOKEN",
  "token_uri": "https://oauth2.googleapis.com/token",
  "calendar_id": "primary",
  "sender_filter": "noreply@message.sail.ca",
  "timezone": "America/Toronto"
}
```
- Name the secret: work-schedule-bot
    - Save
5) Vendor Lambda dependencies

```bash
pip install -r lambda/requirements.txt -t lambda/
```
This puts third-party libs into lambda/ so Terraform can zip them with your code. The repo’s .gitignore is set to ignore vendored libs (and secrets) so they don’t get committed.

6) Deploy with Terraform
- Initialize providers
```bash
cd infra
terraform init
```
- Apply
```bash
terraform apply \
  -var region=us-east-2 \
  -var secret_name=work-schedule-bot
```

- This creates:
    - IAM role & policy (Lambda exec + read your secret)
    - Lambda function (zips lambda/ directory)
    - CloudWatch log group
    - EventBridge weekly schedule (default: Sundays 10am Toronto)
    - Permission for EventBridge to invoke Lambda

7) First run & logs
- Manual one-off invoke
```bash
aws lambda invoke \
  --function-name work-schedule-bot \
  --region us-east-2 \
  --payload '{}' \
  out.json && cat out.json
```
- Tail logs
```bash
aws logs tail /aws/lambda/work-schedule-bot --region us-east-2 --since 15m --follow
```

You should see a result like:
```bash
{"ok": true, "created": 3, "updated": 0}
```

8) Run a quick test
- Temporarily change run frequency to every minute
```bash
terraform apply \
  -var region=us-east-2 \
  -var secret_name=work-schedule-bot \
  -var 'schedule_expression=rate(1 minute)'
```
- After a minute, check your calendar and the created work event should be there
- Revert frequency back to default
```bash
terraform apply \
  -var region=us-east-2 \
  -var secret_name=work-schedule-bot \
  -var 'schedule_expression=cron(0 14 ? * SUN *)'
```




