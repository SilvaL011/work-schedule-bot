variable "region" {
  type = string
  # e.g., "us-east-2"
}

variable "secret_name" {
  type = string
  # e.g., "work-schedule-bot"
}

variable "lambda_name" {
  type    = string
  default = "work-schedule-bot"
}

# Sundays 14:00 UTC (~10am Toronto during DST, ~9am standard)
variable "schedule_expression" {
  type    = string
  default = "cron(0 14 ? * SUN *)"
}
