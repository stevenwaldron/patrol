output "vpc_id" {
  value = aws_vpc.main.id
}

output "vpc_cidr" {
  value = aws_vpc.main.cidr_block
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "database_subnet_ids" {
  value = aws_subnet.database[*].id
}

output "nat_gateway_ids" {
  value = aws_nat_gateway.main[*].id
}

output "internet_gateway_id" {
  value = aws_internet_gateway.main.id
}

output "private_route_table_ids" {
  description = "One route table per private subnet (used for VPC gateway endpoints)."
  value       = aws_route_table.private[*].id
}
