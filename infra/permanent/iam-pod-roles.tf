# ---------------------------------------------------------------------------
# IAM roles for EKS Pod Identity: one "keycard" per workload.
#
# These live in the PERMANENT layer because nothing in them depends on the
# cluster: Pod Identity trusts the EKS service (pods.eks.amazonaws.com), not a
# per-cluster OIDC provider. So the roles survive every teardown; only the
# associations ("badge list") are rebuilt with the cluster.
#
# Each trust policy also pins the exact namespace + ServiceAccount (session tags
# Pod Identity adds, per AWS docs), so a role can only ever be used by the one
# badge it was made for, even if an association were created by mistake.
# ---------------------------------------------------------------------------

locals {
  pod_roles = {
    scheduler = {
      namespace       = "patrol"
      service_account = "scheduler"
      description     = "Patrol scheduler: post patrol orders to SQS"
    }
    worker = {
      namespace       = "patrol"
      service_account = "worker"
      description     = "Patrol worker: take orders from SQS, write results to DynamoDB"
    }
    status-page = {
      namespace       = "patrol"
      service_account = "status-page"
      description     = "Patrol status page: read results from DynamoDB"
    }
    aws-load-balancer-controller = {
      namespace       = "kube-system"
      service_account = "aws-load-balancer-controller"
      description     = "AWS Load Balancer Controller: manage ALBs for Ingresses"
    }
    external-dns = {
      namespace       = "external-dns"
      service_account = "external-dns"
      description     = "ExternalDNS: point Patrol's domain at the current ALB"
    }
  }

  # Least privilege: each workload gets exactly the actions its code calls.
  pod_role_policies = {
    scheduler = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid      = "SendPatrolOrders"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"] # also covers SendMessageBatch
        Resource = aws_sqs_queue.checks.arn
      }]
    })

    worker = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "TakePatrolOrders"
          Effect   = "Allow"
          Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage"]
          Resource = aws_sqs_queue.checks.arn
        },
        {
          Sid      = "SaveResults"
          Effect   = "Allow"
          Action   = ["dynamodb:PutItem"]
          Resource = aws_dynamodb_table.checks.arn
        }
      ]
    })

    status-page = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid      = "ReadResults"
        Effect   = "Allow"
        Action   = ["dynamodb:Query"]
        Resource = aws_dynamodb_table.checks.arn
      }]
    })

    # From the ExternalDNS AWS guide, narrowed: record changes and reads only in
    # Patrol's own zone (the guide allows every hosted zone in the account).
    # Listing zones can't be scoped to one zone, so that one action is "*".
    external-dns = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid    = "ManagePatrolRecords"
          Effect = "Allow"
          Action = [
            "route53:ChangeResourceRecordSets",
            "route53:ListResourceRecordSets",
            "route53:ListTagsForResources",
          ]
          Resource = aws_route53_zone.main.arn
        },
        {
          Sid      = "FindZones"
          Effect   = "Allow"
          Action   = ["route53:ListHostedZones"]
          Resource = "*"
        }
      ]
    })

    # Official policy published with the controller, pinned to the controller
    # version we install (file name = version). Upgrading the controller means
    # diffing its new iam_policy.json against this file and replacing it if changed.
    aws-load-balancer-controller = file("${path.module}/policies/aws-load-balancer-controller-v3.6.2.json")
  }
}

data "aws_iam_policy_document" "pod_trust" {
  for_each = local.pod_roles

  statement {
    sid     = "AllowEksPodIdentity"
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes-namespace"
      values   = [each.value.namespace]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes-service-account"
      values   = [each.value.service_account]
    }
  }
}

resource "aws_iam_role" "pod" {
  for_each = local.pod_roles

  name               = "${var.name_prefix}-${each.key}"
  description        = each.value.description
  assume_role_policy = data.aws_iam_policy_document.pod_trust[each.key].json

  depends_on = [terraform_data.account_guard]
}

resource "aws_iam_policy" "pod" {
  for_each = local.pod_roles

  name        = "${var.name_prefix}-${each.key}"
  description = each.value.description
  policy      = local.pod_role_policies[each.key]
}

resource "aws_iam_role_policy_attachment" "pod" {
  for_each = local.pod_roles

  role       = aws_iam_role.pod[each.key].name
  policy_arn = aws_iam_policy.pod[each.key].arn
}
