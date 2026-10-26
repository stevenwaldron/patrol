# ---------------------------------------------------------------------------
# VPC gateway endpoints: private doors from the private subnets straight to
# DynamoDB and S3, bypassing the NAT gateway. Gateway endpoints are free.
#
# Why: without them, a NAT gateway failure would break every check (DynamoDB
# writes), the status page (DynamoDB reads) and new pods starting (ECR stores
# image layers in S3). They also cut NAT data-processing charges.
#
# Endpoint policies are left at the default (full access through the door):
# who may do what is already decided by each pod's IAM role, and a restrictive
# S3 endpoint policy would also have to allow ECR's own layer buckets.
# ---------------------------------------------------------------------------

resource "aws_vpc_endpoint" "gateway" {
  for_each = toset(["dynamodb", "s3"])

  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids

  tags = {
    Name = "${var.cluster_name}-${each.key}-endpoint"
  }
}
