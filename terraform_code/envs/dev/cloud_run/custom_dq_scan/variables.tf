variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-east4"
}

variable "location" {
  type    = string
  default = "us-east4"
}

variable "terraform_sa" {
  type = string
}

variable "image_tag" {
  description = "The commit hash tag for the container image (set by Jenkins/CI)"
  type        = string
  # No default — fails fast if not provided by CI
}

variable "gcs_bucket_name" {
  description = "GCS bucket holding custom_dq.csv"
  type        = string
  default     = "jyav-dev-dgsdo-0-usre-gkcenrichment"
}
