data "aws_region" "current" {}

locals {
  cw_region = data.aws_region.current.name

  pipeline_dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Kinesis consumer — invocations & errors"
          region = local.cw_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", aws_lambda_function.kinesis_consumer.function_name],
            [".", "Errors", ".", "."],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Ingestion outcomes (custom metrics)"
          region = local.cw_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["DeviceLogPipeline", "RecordsProcessed"],
            [".", "RecordsRejected"],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "DLQ depth"
          region = local.cw_region
          view   = "timeSeries"
          stat   = "Maximum"
          period = 60
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.ingestion_dlq.name],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "ETL executions (succeeded vs failed)"
          region = local.cw_region
          view   = "timeSeries"
          stat   = "Sum"
          period = 300
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.etl.arn],
            [".", "ExecutionsFailed", ".", "."],
          ]
        }
      },
    ]
  })
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${var.project_name}-pipeline"
  dashboard_body = local.pipeline_dashboard_body
}

# Alert if rejected records are over 50 in 5 minutes.
resource "aws_cloudwatch_metric_alarm" "records_rejected" {
  alarm_name          = "${var.project_name}-records-rejected"
  alarm_description   = "RecordsRejected exceeded 50 in a 5-minute window."
  namespace           = "DeviceLogPipeline"
  metric_name         = "RecordsRejected"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 50
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.pipeline_alerts.arn]
}
