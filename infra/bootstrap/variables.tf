variable "region" {
  description = "AWS region for Patrol."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Local AWS CLI profile that assumes into the Patrol account."
  type        = string
  default     = "patrol"
}

variable "expected_account_id" {
  description = "Safety check: apply fails if the profile points at any other account."
  type        = string
  default     = "419701112481"
}

variable "name_prefix" {
  description = "Prefix for every Patrol resource name."
  type        = string
  default     = "patrol"
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget for the whole Patrol account, in USD."
  type        = number
  default     = 30
}

variable "budget_alert_email" {
  description = "Email address that receives budget alerts. Set in terraform.tfvars (not committed)."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_alert_email))
    error_message = "budget_alert_email must be a valid email address."
  }
}
