data "aws_caller_identity" "current" {}

# Refuse to plan/apply anything if credentials resolved to the wrong account.
resource "terraform_data" "account_guard" {
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.expected_account_id
      error_message = "Wrong AWS account: credentials point at ${data.aws_caller_identity.current.account_id}, expected ${var.expected_account_id}. Did you prefix the command with AWS_PROFILE=patrol?"
    }
  }
}
