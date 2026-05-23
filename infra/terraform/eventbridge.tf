data "aws_iam_policy_document" "eventbridge_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "eventbridge_etl" {
  name               = "${var.project_name}-eventbridge-etl"
  assume_role_policy = data.aws_iam_policy_document.eventbridge_assume.json
}

data "aws_iam_policy_document" "eventbridge_etl_inline" {
  statement {
    sid       = "StartETL"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.etl.arn]
  }
}

resource "aws_iam_role_policy" "eventbridge_etl_inline" {
  name   = "${var.project_name}-eventbridge-etl-inline"
  role   = aws_iam_role.eventbridge_etl.id
  policy = data.aws_iam_policy_document.eventbridge_etl_inline.json
}

resource "aws_cloudwatch_event_rule" "hourly_etl" {
  name                = "${var.project_name}-hourly-etl"
  description         = "Fires the device-log ETL state machine every hour at :05."
  schedule_expression = "cron(5 * * * ? *)"
}

resource "aws_cloudwatch_event_target" "hourly_etl" {
  rule     = aws_cloudwatch_event_rule.hourly_etl.name
  arn      = aws_sfn_state_machine.etl.arn
  role_arn = aws_iam_role.eventbridge_etl.arn
}
