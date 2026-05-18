data "aws_caller_identity" "current" {}

locals {
  data_zones = ["raw", "staging", "curated"]
}

resource "aws_s3_bucket" "data" {
  for_each = toset(local.data_zones)

  bucket = "${var.project_name}-${each.value}-${data.aws_caller_identity.current.account_id}"

  # force_destroy lets `terraform destroy` clear non-empty buckets during dev
  # iteration. Acceptable here because data is regenerable by the generator CLI.
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  for_each = aws_s3_bucket.data

  bucket = each.value.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
