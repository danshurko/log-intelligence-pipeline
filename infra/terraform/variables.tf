variable "project_name" {
  description = "Prefix used for naming all project resources."
  type        = string
  default     = "device-log"
}

variable "region" {
  description = "AWS region in which all resources are created."
  type        = string
  default     = "eu-north-1"
}

variable "notification_email" {
  description = "Email subscribed to pipeline-alerts SNS topic. Set via terraform.tfvars or TF_VAR_notification_email. Null skips the subscription."
  type        = string
  nullable    = true
  default     = null
}
