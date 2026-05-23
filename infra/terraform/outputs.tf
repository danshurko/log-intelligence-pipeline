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

output "artifacts_bucket_name" {
  description = "Name of the build-artifacts S3 bucket (Lambda layers, Glue scripts)."
  value       = aws_s3_bucket.data["artifacts"].id
}

output "stream_name" {
  description = "Name of the Kinesis stream that receives device events."
  value       = aws_kinesis_stream.device_logs.name
}

output "lambda_function_name" {
  description = "Name of the Lambda function that consumes the Kinesis stream."
  value       = aws_lambda_function.kinesis_consumer.function_name
}

output "dlq_url" {
  description = "URL of the SQS dead-letter queue for failed ingestion batches."
  value       = aws_sqs_queue.ingestion_dlq.url
}

output "dlq_name" {
  description = "Name of the SQS dead-letter queue (for CloudWatch metric lookups)."
  value       = aws_sqs_queue.ingestion_dlq.name
}

output "aws_region" {
  description = "Region the stack is deployed into."
  value       = var.region
}

output "crawler_name" {
  description = "Name of the Glue Crawler that indexes the raw zone."
  value       = aws_glue_crawler.raw.name
}

output "state_machine_arn" {
  description = "ARN of the ETL Step Functions state machine."
  value       = aws_sfn_state_machine.etl.arn
}

output "pipeline_alerts_topic_arn" {
  description = "SNS topic that receives ETL failure notifications."
  value       = aws_sns_topic.pipeline_alerts.arn
}
