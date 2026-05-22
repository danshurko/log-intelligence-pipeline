resource "aws_sqs_queue" "ingestion_dlq" {
  name                      = "${var.project_name}-ingestion-dlq"
  message_retention_seconds = 1209600
}
