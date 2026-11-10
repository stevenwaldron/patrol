# ---------------------------------------------------------------------------
# AWS Fault Injection Service (Phase 9, drill 4): a real Spot interruption for
# the Karpenter worker node. AWS sends the rebalance recommendation and the
# 2-minute interruption notice through the same EventBridge path as a genuine
# reclaim, so it tests Karpenter's interruption queue end to end.
#
# Run it (cost: about $0.10, FIS bills per action-minute):
#   aws fis start-experiment --profile patrol \
#     --experiment-template-id "$(terraform output -raw spot_interruption_experiment)"
#
# Only an experiment TEMPLATE lives here; nothing happens until it's started.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "fis_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["fis.amazonaws.com"]
    }
    # Only experiments in this account can use the role (confused-deputy guard).
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "fis_spot" {
  name               = "patrol-fis-spot-interruption"
  description        = "AWS FIS: send a Spot interruption to Patrol's Karpenter worker nodes (drills)"
  assume_role_policy = data.aws_iam_policy_document.fis_trust.json
}

data "aws_iam_policy_document" "fis_spot" {
  # Find the target instances.
  statement {
    sid       = "FindInstances"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }
  # Interrupt only Karpenter worker nodes of THIS cluster (tags Karpenter puts
  # on every instance it launches), never the managed nodes or anything else.
  statement {
    sid       = "InterruptPatrolWorkerSpotNodes"
    actions   = ["ec2:SendSpotInstanceInterruptions"]
    resources = ["arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/eks:eks-cluster-name"
      values   = [var.cluster_name]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/karpenter.sh/nodepool"
      values   = ["workers"]
    }
  }
}

resource "aws_iam_role_policy" "fis_spot" {
  name   = "send-spot-interruptions"
  role   = aws_iam_role.fis_spot.id
  policy = data.aws_iam_policy_document.fis_spot.json
}

resource "aws_fis_experiment_template" "spot_interruption" {
  description = "Patrol drill 4: interrupt one Karpenter worker Spot node (2-minute notice)"
  role_arn    = aws_iam_role.fis_spot.arn

  # No automatic stop condition: the action is a single, bounded event.
  stop_condition {
    source = "none"
  }

  action {
    name      = "interrupt-worker-node"
    action_id = "aws:ec2:send-spot-instance-interruptions"
    # The real notice period: AWS's minimum, and what a genuine reclaim gives.
    parameter {
      key   = "durationBeforeInterruption"
      value = "PT2M"
    }
    target {
      key   = "SpotInstances"
      value = "patrol-worker-spot-node"
    }
  }

  target {
    name           = "patrol-worker-spot-node"
    resource_type  = "aws:ec2:spot-instance"
    selection_mode = "COUNT(1)"
    resource_tag {
      key   = "karpenter.sh/nodepool"
      value = "workers"
    }
    resource_tag {
      key   = "eks:eks-cluster-name"
      value = var.cluster_name
    }
    filter {
      path   = "State.Name"
      values = ["running"]
    }
  }

  tags = {
    Name = "patrol-spot-interruption"
  }
}
