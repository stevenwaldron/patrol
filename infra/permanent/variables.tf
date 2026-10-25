variable "region" {
  description = "AWS region for Patrol."
  type        = string
  default     = "us-east-1"
}

variable "expected_account_id" {
  description = "Safety check: plan/apply fails if credentials point at any other account."
  type        = string
  default     = "419701112481"
}

variable "name_prefix" {
  description = "Prefix for every Patrol resource name."
  type        = string
  default     = "patrol"
}

variable "services" {
  description = "Patrol's services; each gets its own container registry."
  type        = list(string)
  default     = ["status-page", "scheduler", "worker"]
}

variable "images_to_keep" {
  description = "How many tagged images to keep per registry before the oldest expire."
  type        = number
  default     = 20
}

variable "check_message_retention_seconds" {
  description = "How long a patrol order may wait in the queue. Orders are issued every minute, so stale ones are useless."
  type        = number
  default     = 900 # 15 minutes
}

variable "check_visibility_timeout_seconds" {
  description = "How long a worker has to finish one check before the order is handed to another worker."
  type        = number
  default     = 60
}

variable "max_receive_count" {
  description = "Delivery attempts before an order is moved to the dead-letter queue."
  type        = number
  default     = 3
}
