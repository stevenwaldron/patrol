# ---------------------------------------------------------------------------
# DNS + TLS for Patrol's public domain.
#
# The domain is registered at IONOS; its DNS is delegated to this Route 53 zone
# (one manual step: paste the zone's 4 name servers into IONOS). Both pieces live
# in the permanent layer: the zone's name servers and the certificate must stay
# the same across every cluster rebuild.
#
# In the cluster, ExternalDNS points the domain at each rebuild's new ALB, and
# the AWS Load Balancer Controller finds this certificate by hostname.
# ---------------------------------------------------------------------------

resource "aws_route53_zone" "main" {
  name    = var.domain_name
  comment = "Patrol public zone (delegated from IONOS)"

  # Recreating the zone would give it NEW name servers and break the IONOS
  # delegation until updated by hand. Terraform refuses to destroy it.
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.account_guard]
}

# Free public certificate for the domain and every subdomain. ACM renews it by
# itself as long as the validation record below stays in the zone.
resource "aws_acm_certificate" "main" {
  domain_name               = var.domain_name
  subject_alternative_names = ["*.${var.domain_name}"]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# The record ACM checks to prove we control the domain. The apex and the
# wildcard ask for the same record, so the second write overwrites the first.
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.main.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      record = dvo.resource_record_value
    }
  }

  zone_id         = aws_route53_zone.main.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 300
  allow_overwrite = true
}

# No aws_acm_certificate_validation resource on purpose: it would make this
# apply wait until the IONOS delegation (a manual step that comes AFTER this
# apply) has spread. ACM issues the certificate on its own once it can see the
# record; infra/up.sh refuses to build the cluster until it is ISSUED.
