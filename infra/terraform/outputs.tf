output "raw_bucket_name" {
  description = "Name of the raw zone S3 bucket."
  value       = aws_s3_bucket.data["raw"].id
}

output "staging_bucket_name" {
  description = "Name of the staging zone S3 bucket."
  value       = aws_s3_bucket.data["staging"].id
}

output "curated_bucket_name" {
  description = "Name of the curated zone S3 bucket."
  value       = aws_s3_bucket.data["curated"].id
}
