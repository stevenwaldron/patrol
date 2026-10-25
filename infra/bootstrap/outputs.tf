output "account_id" {
  description = "Account the bootstrap was applied to."
  value       = data.aws_caller_identity.current.account_id
}

output "tfstate_bucket_name" {
  description = "S3 bucket holding Terraform state for the permanent and temporary layers."
  value       = aws_s3_bucket.tfstate.bucket
}

output "budget_name" {
  description = "Name of the monthly cost budget."
  value       = aws_budgets_budget.monthly.name
}
