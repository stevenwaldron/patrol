output "ecr_repository_urls" {
  description = "Registry URL per service (images are pushed here, tagged by commit ID)."
  value       = { for k, r in aws_ecr_repository.service : k => r.repository_url }
}

output "checks_queue_url" {
  description = "SQS queue the scheduler sends patrol orders to."
  value       = aws_sqs_queue.checks.url
}

output "checks_queue_arn" {
  value = aws_sqs_queue.checks.arn
}

output "checks_dlq_arn" {
  value = aws_sqs_queue.checks_dlq.arn
}

output "checks_table_name" {
  description = "DynamoDB table holding check results."
  value       = aws_dynamodb_table.checks.name
}

output "checks_table_arn" {
  value = aws_dynamodb_table.checks.arn
}

output "slack_webhook_secret_arn" {
  description = "Secrets Manager secret for the Slack webhook (value set via CLI)."
  value       = aws_secretsmanager_secret.slack_webhook.arn
}

# Read by the cluster layer (terraform_remote_state) to create the Pod Identity
# associations, so the namespace/ServiceAccount pairing lives in one place.
output "pod_identity_roles" {
  description = "Pod Identity role per workload: ARN plus the namespace/ServiceAccount it is locked to."
  value = {
    for k, v in local.pod_roles : k => {
      role_arn        = aws_iam_role.pod[k].arn
      namespace       = v.namespace
      service_account = v.service_account
    }
  }
}
