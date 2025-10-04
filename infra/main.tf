#Terraform definition of your AWS resources

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 5.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.3" }
  }
}

provider "aws" {
  region = var.region
}

# Zip your entire lambda/ dir (code + vendored libs)
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/lambda.zip"
}

# Execution role
resource "aws_iam_role" "lambda" {
  name = "${var.lambda_name}-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect    = "Allow",
      Principal = { Service = "lambda.amazonaws.com" },
      Action    = "sts:AssumeRole"
    }]
  })
}

# Minimal permissions: logs + read your secret
resource "aws_iam_role_policy" "lambda_inline" {
  name = "${var.lambda_name}-policy"
  role = aws_iam_role.lambda.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow",
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = "*"
      },
      {
        Effect   = "Allow",
        Action   = ["secretsmanager:GetSecretValue"],
        Resource = "arn:aws:secretsmanager:us-east-2:059942063628:secret:work-schedule-bot-3LG3Ob" # tighten to your secret ARN later
      }
    ]
  })
}

# Log group for tidy retention
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.lambda_name}"
  retention_in_days = 14
}

# Lambda function
resource "aws_lambda_function" "bot" {
  function_name    = var.lambda_name
  role             = aws_iam_role.lambda.arn
  handler          = "main.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  timeout          = 60

  environment {
  variables = {
    SECRET_NAME    = var.secret_name
    EVENT_COLOR_ID = "6"
    SHIFT_TITLE    = "Work"
    SUBJECT_FILTER = "Publish Schedule Notification"
  }
}
}

# Weekly schedule — default from variables.tf (Sundays 14:00 UTC)
resource "aws_cloudwatch_event_rule" "weekly" {
  name                = "${var.lambda_name}-weekly"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "invoke_lambda" {
  rule      = aws_cloudwatch_event_rule.weekly.name
  target_id = "lambda"
  arn       = aws_lambda_function.bot.arn
}

resource "aws_lambda_permission" "allow_events" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.weekly.arn
}