variable "project_id" {
  type = string
}

variable "location" {
  type    = string
  default = "us-central1"
}

variable "data_scan_id" {
  type = string
}

variable "display_name" {
  type = string
}

variable "description" {
  type    = string
  default = ""
}

variable "labels" {
  type    = map(string)
  default = {}
}

variable "source_bq_table" {
  type = string
}

variable "results_bq_table" {
  type = string
}

variable "schedule_cron" {
  type    = string
  default = null
}

variable "sampling_percent" {
  type    = number
  default = 100.0
}

variable "row_filter" {
  type    = string
  default = null
}
