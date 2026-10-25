# ---------------------------------------------------------------------------
# One private container registry per service.
#   - IMMUTABLE tags: a commit-ID tag can never be overwritten, so a tag always
#     means exactly one build (needed for reliable rollback).
#   - Basic scan on push (free). Trivy in CI is the main gate; this is a backstop.
# ---------------------------------------------------------------------------
resource "aws_ecr_repository" "service" {
  for_each = toset(var.services)

  name                 = "${var.name_prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  depends_on = [terraform_data.account_guard]
}

# Keep storage (and cost) bounded: untagged layers go after a day,
# and only the newest N tagged images are kept.
resource "aws_ecr_lifecycle_policy" "service" {
  for_each   = aws_ecr_repository.service
  repository = each.value.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Remove untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the newest ${var.images_to_keep} images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.images_to_keep
        }
        action = { type = "expire" }
      }
    ]
  })
}
