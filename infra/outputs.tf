#Exposes useful info after terraform apply
output "lambda_arn" {
  value = aws_lambda_function.bot.arn
}
