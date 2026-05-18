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
