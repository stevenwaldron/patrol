# ---------------------------------------------------------------------------
# Pod Identity associations: the "badge list" linking each ServiceAccount to
# its IAM role. Rebuilt with every cluster; the roles themselves live in the
# permanent layer and never change.
#
# The role list (ARN + namespace + ServiceAccount) is read from the permanent
# layer's state, so the pairing is defined once and can't drift between layers.
# ---------------------------------------------------------------------------

data "terraform_remote_state" "permanent" {
  backend = "s3"

  config = {
    bucket = "patrol-tfstate-419701112481"
    key    = "permanent/terraform.tfstate"
    region = "us-east-1"
  }
}

resource "aws_eks_pod_identity_association" "this" {
  for_each = data.terraform_remote_state.permanent.outputs.pod_identity_roles

  cluster_name    = module.eks.cluster_name
  namespace       = each.value.namespace
  service_account = each.value.service_account
  role_arn        = each.value.role_arn
}
