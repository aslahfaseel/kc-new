variable "project_id" {
  description = "The ID of the GCP project"
  type        = string
}

variable "location" {
  description = "The region to deploy the Cloud Run Job"
  type        = string
  default     = "us-central1"
}

variable "job_name" {
  description = "The name of the Cloud Run Job"
  type        = string
}

variable "container_image" {
  description = "The URI of the container image to run"
  type        = string
}

variable "service_account_email" {
  description = "The service account email to run the job as"
  type        = string
}

variable "env_vars" {
  description = "A map of environment variables to pass to the container"
  type        = map(string)
  default     = {}
}

variable "max_retries" {
  description = "Maximum number of retries for the job"
  type        = number
  default     = 3
}

variable "timeout_seconds" {
  description = "Timeout for the job in seconds"
  type        = number
  default     = 3600
}

variable "labels" {
  description = "A map of labels to attach to the job"
  type        = map(string)
  default     = {}
}

variable "vpc_connector" {
  description = "The full resource ID of the Serverless VPC Access Connector"
  type        = string
}

variable "vpc_egress" {
  description = "The outbound network traffic routing behavior"
  type        = string
  default     = "ALL_TRAFFIC"
}
