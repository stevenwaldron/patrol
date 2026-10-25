# ---------------------------------------------------------------------------
# Slack webhook for Alertmanager.
#
# Terraform creates only the EMPTY secret. The webhook URL is put in with the
# AWS CLI, so the value never appears in Terraform code, plans or state.
# (Lesson from CloudForger: a saved plan embeds state, so a plan is a secret.)
# Inside the cluster, External Secrets Operator reads it by name.
# ---------------------------------------------------------------------------
resource "aws_secretsmanager_secret" "slack_webhook" {
  name                    = "${var.name_prefix}/slack-webhook"
  description             = "Slack incoming-webhook URL used by Alertmanager. Value set out-of-band, never in Terraform."
  recovery_window_in_days = 7

  depends_on = [terraform_data.account_guard]
}
