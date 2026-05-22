data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "kinesis_consumer" {
  name               = "${var.project_name}-kinesis-consumer"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "kinesis_consumer_basic" {
  role       = aws_iam_role.kinesis_consumer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "kinesis_consumer_inline" {
  statement {
    sid = "KinesisRead"
    actions = [
      "kinesis:DescribeStream",
      "kinesis:DescribeStreamSummary",
      "kinesis:GetRecords",
      "kinesis:GetShardIterator",
      "kinesis:ListShards",
      "kinesis:SubscribeToShard",
    ]
    resources = [aws_kinesis_stream.device_logs.arn]
  }

  statement {
    sid       = "S3RawWrite"
    actions   = ["s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.data["raw"].arn}/*"]
  }

  statement {
    sid       = "DlqSend"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.ingestion_dlq.arn]
  }

  statement {
    sid       = "CloudWatchMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "kinesis_consumer_inline" {
  name   = "${var.project_name}-kinesis-consumer-inline"
  role   = aws_iam_role.kinesis_consumer.id
  policy = data.aws_iam_policy_document.kinesis_consumer_inline.json
}
