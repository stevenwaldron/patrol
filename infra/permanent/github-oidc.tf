# ---------------------------------------------------------------------------
# GitHub Actions -> AWS without stored keys (OIDC).
#
# GitHub signs a short-lived token saying "this is workflow run X of repo Y on
# branch Z"; AWS checks the signature against this identity provider and trades
# it for temporary credentials (about 1 hour). No access keys exist anywhere.
#
# The role trusts ONLY pushes to main of stevenwaldron/patrol, matched exactly
# (no wildcards) on GitHub's immutable subject format, which includes numeric
# IDs: a deleted-and-recreated or renamed repo with the same name gets new IDs
# and can't inherit this trust. Pull requests get no AWS access at all.
#
# Permissions: push images to Patrol's 3 ECR repositories. Nothing else: CI has
# no access to the cluster (deploying is a Git change Argo CD applies) and none
# to Terraform state.
# ---------------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  depends_on = [terraform_data.account_guard]
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    sid     = "GitHubActionsMainBranchOnly"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}:ref:refs/heads/main",
      ]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name                 = "${var.name_prefix}-github-actions"
  description          = "GitHub Actions (main branch of ${var.github_owner}/${var.github_repo}): push images to ECR"
  assume_role_policy   = data.aws_iam_policy_document.github_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_ecr_push" {
  statement {
    sid       = "EcrLogin"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"] # can't be scoped to a repository
    resources = ["*"]
  }

  statement {
    sid    = "PushPatrolImages"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
      "ecr:DescribeImageScanFindings",
    ]
    resources = [for r in aws_ecr_repository.service : r.arn]
  }
}

resource "aws_iam_role_policy" "github_ecr_push" {
  name   = "ecr-push"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_ecr_push.json
}
