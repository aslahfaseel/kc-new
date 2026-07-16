variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "location" {
  type    = string
  default = "us-central1"
}

variable "terraform_sa" {
  type = string
}

variable "dataplex_service_agent" {
  type = string
}

variable "alert_emails" {
  type    = list(string)
  default = []
}

variable "dq_profile_scans" {
  description = "config mapping"
  type        = any
  default     = {}  
}

variable "gcs_bucket_name" {
  description = "GCS bucket that holds profiling.csv, custom_dq.csv, profile_based_dq.csv"
  type        = string
  default     = "vz-datacatalog"
}

#testing cicd.....
