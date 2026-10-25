# ---------------------------------------------------------------------------
# Check results. One item per check:
#   site_id (partition key)  e.g. "cloudforger"
#   checked_at (sort key)    ISO-8601 UTC timestamp, so items sort by time
#   expires_at               epoch seconds; DynamoDB deletes the item after it
#
# "Latest status of a site" = query site_id, newest first, limit 1.
# "History of a site"       = query site_id with a checked_at range.
# Lives in the permanent layer so history survives every cluster teardown.
# ---------------------------------------------------------------------------
resource "aws_dynamodb_table" "checks" {
  name         = "${var.name_prefix}-checks"
  billing_mode = "PAY_PER_REQUEST" # no idle cost; this workload is tiny and bursty
  hash_key     = "site_id"
  range_key    = "checked_at"

  attribute {
    name = "site_id"
    type = "S"
  }

  attribute {
    name = "checked_at"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = true

  depends_on = [terraform_data.account_guard]
}
