output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "kubernetes_version" {
  value = module.eks.cluster_version
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "private_subnet_ids" {
  value = module.vpc.private_subnet_ids
}

output "node_security_group_id" {
  value = module.eks.node_security_group_id
}

output "configure_kubectl" {
  value = "aws eks update-kubeconfig --name ${module.eks.cluster_name} --region ${var.region} --profile patrol --alias patrol"
}

output "pod_identity_associations" {
  description = "ServiceAccount -> IAM role links created for this cluster."
  value       = { for k, a in aws_eks_pod_identity_association.this : k => "${a.namespace}/${a.service_account}" }
}

output "gateway_endpoints" {
  value = { for k, e in aws_vpc_endpoint.gateway : k => e.id }
}

output "karpenter" {
  description = "What the Git-side Karpenter config refers to."
  value = {
    controller_role  = module.karpenter.iam_role_name
    node_role        = module.karpenter.node_iam_role_name
    instance_profile = module.karpenter.instance_profile_name
    interruption_sqs = module.karpenter.queue_name
  }
}

output "spot_interruption_experiment" {
  description = "AWS FIS experiment template ID for drill 4 (interrupt one worker Spot node)."
  value       = aws_fis_experiment_template.spot_interruption.id
}
