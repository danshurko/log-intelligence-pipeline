resource "aws_kinesis_stream" "device_logs" {
  name             = "${var.project_name}-device-logs"
  retention_period = 24

  stream_mode_details {
    stream_mode = "ON_DEMAND"
  }
}
