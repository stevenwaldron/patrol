# ---------------------------------------------------------------------------
# The patrol-order queue: the scheduler posts one message per site per minute,
# the workers take them. Orders that fail repeatedly land in a dead-letter queue
# instead of retrying forever, so a single bad site can't jam the workers.
# ---------------------------------------------------------------------------
resource "aws_sqs_queue" "checks_dlq" {
  name                      = "${var.name_prefix}-checks-dlq"
  message_retention_seconds = 345600 # 4 days, time to inspect what went wrong
  sqs_managed_sse_enabled   = true

  depends_on = [terraform_data.account_guard]
}

resource "aws_sqs_queue" "checks" {
  name                       = "${var.name_prefix}-checks"
  message_retention_seconds  = var.check_message_retention_seconds
  visibility_timeout_seconds = var.check_visibility_timeout_seconds
  receive_wait_time_seconds  = 20 # long polling: fewer empty requests, lower cost
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.checks_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })
}

# Only the main queue may send failures into the dead-letter queue.
resource "aws_sqs_queue_redrive_allow_policy" "checks_dlq" {
  queue_url = aws_sqs_queue.checks_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.checks.arn]
  })
}
