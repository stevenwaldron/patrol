# Origin: modules/vpc from Steven's earlier terraform-vpc-project (Aug 2026),
# copied into Patrol and extended for EKS. All changes are backward compatible:
#   1. optional public/private subnet tags (EKS load balancer discovery)
#   2. the database route table is only created when database subnets exist
#      (with a `moved` block so existing state of the original project is kept)

locals {
  name = "${var.project_name}-${var.environment}"
}

# --- VPC ---

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${local.name}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name}-igw"
  }
}

# --- Subnets ---

resource "aws_subnet" "public" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge({
    Name = "${local.name}-public-${var.azs[count.index]}"
    Tier = "public"
  }, var.public_subnet_tags)
}

resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.azs[count.index]

  tags = merge({
    Name = "${local.name}-private-${var.azs[count.index]}"
    Tier = "private"
  }, var.private_subnet_tags)
}

resource "aws_subnet" "database" {
  count             = length(var.database_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.database_subnet_cidrs[count.index]
  availability_zone = var.azs[count.index]

  tags = {
    Name = "${local.name}-database-${var.azs[count.index]}"
    Tier = "database"
  }
}

# --- NAT Gateways ---
# One EIP + NAT gateway per AZ by default, or a single shared one if
# var.single_nat_gateway is true (cheaper, but a single point of failure).

resource "aws_eip" "nat" {
  count  = var.single_nat_gateway ? 1 : length(var.public_subnet_cidrs)
  domain = "vpc"

  tags = {
    Name = "${local.name}-nat-eip-${count.index}"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count         = var.single_nat_gateway ? 1 : length(var.public_subnet_cidrs)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "${local.name}-nat-${count.index}"
  }

  depends_on = [aws_internet_gateway.main]
}

# --- Route Tables: Public ---

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name}-public-rt"
  }
}

resource "aws_route" "public_internet_access" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- Route Tables: Private (one per AZ, routes through that AZ's NAT) ---

resource "aws_route_table" "private" {
  count  = length(var.private_subnet_cidrs)
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name}-private-rt-${var.azs[count.index]}"
  }
}

resource "aws_route" "private_nat_access" {
  count                  = length(var.private_subnet_cidrs)
  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = var.single_nat_gateway ? aws_nat_gateway.main[0].id : aws_nat_gateway.main[count.index].id
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# --- Route Table: Database (no internet route, local only) ---

resource "aws_route_table" "database" {
  count  = length(var.database_subnet_cidrs) > 0 ? 1 : 0
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name}-database-rt"
  }
}

resource "aws_route_table_association" "database" {
  count          = length(aws_subnet.database)
  subnet_id      = aws_subnet.database[count.index].id
  route_table_id = aws_route_table.database[0].id
}

# The database route table gained a `count`, which changes its address from
# aws_route_table.database to aws_route_table.database[0]. This tells Terraform
# it's the same object, so existing deployments keep it instead of replacing it.
moved {
  from = aws_route_table.database
  to   = aws_route_table.database[0]
}
