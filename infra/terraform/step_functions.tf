data "aws_iam_policy_document" "state_machine_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "state_machine" {
  name               = "${var.project_name}-etl-sfn"
  assume_role_policy = data.aws_iam_policy_document.state_machine_assume.json
}

data "aws_iam_policy_document" "state_machine_inline" {
  statement {
    sid = "GlueOrchestrate"
    actions = [
      "glue:StartCrawler",
      "glue:GetCrawler",
      "glue:StartJobRun",
      "glue:GetJobRun",
      "glue:GetJobRuns",
      "glue:BatchStopJobRun",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "InvokeVerify"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.verify.arn]
  }

  statement {
    sid       = "PublishAlerts"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.pipeline_alerts.arn]
  }

  # Required for `glue:startJobRun.sync` managed EventBridge rule calls.
  statement {
    sid = "ManagedEventsRule"
    actions = [
      "events:PutTargets",
      "events:PutRule",
      "events:DescribeRule",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "state_machine_inline" {
  name   = "${var.project_name}-etl-sfn-inline"
  role   = aws_iam_role.state_machine.id
  policy = data.aws_iam_policy_document.state_machine_inline.json
}

resource "aws_sfn_state_machine" "etl" {
  name     = "${var.project_name}-etl"
  role_arn = aws_iam_role.state_machine.arn

  definition = jsonencode({
    Comment = "Hourly device-log ETL: Crawler -> Glue PySpark -> Verify."
    StartAt = "RunCrawler"
    States = {
      RunCrawler = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:glue:startCrawler"
        Parameters = {
          Name = aws_glue_crawler.raw.name
        }
        # If crawler is already running, just wait and continue.
        Catch = [
          {
            ErrorEquals = ["Glue.CrawlerRunningException"]
            Next        = "WaitForCrawler"
          },
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "NotifyFailure"
          },
        ]
        ResultPath = null
        Next       = "WaitForCrawler"
      }

      WaitForCrawler = {
        Type    = "Wait"
        Seconds = 30
        Next    = "GetCrawlerStatus"
      }

      GetCrawlerStatus = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:glue:getCrawler"
        Parameters = {
          Name = aws_glue_crawler.raw.name
        }
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "NotifyFailure"
          },
        ]
        Next = "IsCrawlerReady"
      }

      IsCrawlerReady = {
        Type = "Choice"
        Choices = [
          {
            Variable     = "$.Crawler.State"
            StringEquals = "READY"
            Next         = "RunGlueJob"
          },
        ]
        Default = "WaitForCrawler"
      }

      RunGlueJob = {
        Type     = "Task"
        Resource = "arn:aws:states:::glue:startJobRun.sync"
        Parameters = {
          JobName = aws_glue_job.transform.name
        }
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "NotifyFailure"
          },
        ]
        ResultPath = "$.glueJobRun"
        Next       = "VerifyCounts"
      }

      VerifyCounts = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.verify.arn
          Payload      = {}
        }
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "NotifyFailure"
          },
        ]
        End = true
      }

      NotifyFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"
        Parameters = {
          TopicArn    = aws_sns_topic.pipeline_alerts.arn
          Subject     = "${var.project_name} ETL failed"
          "Message.$" = "States.JsonToString($)"
        }
        End = true
      }
    }
  })
}
