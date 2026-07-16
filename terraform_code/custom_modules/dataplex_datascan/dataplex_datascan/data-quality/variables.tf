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

# Optional: override the default Dataplex service agent with a custom SA
variable "service_account_email" {
  type        = string
  default     = null
  description = "Custom service account email to run the scan. Defaults to the Dataplex Service Agent."
}

# DQ rules — supports static (row_condition_sql) and all 9 profile-based rule types
variable "dq_rules" {
  type = list(object({
    name                       = string
    dimension                  = string
    threshold                  = optional(number, 1.0)
    description                = optional(string, "")
    column                     = optional(string, null)
    ignore_null                = optional(bool, null)

    # ── Static rule types ──────────────────────────────────────────────────
    row_condition_sql           = optional(string, null)
    table_condition_sql         = optional(string, null)
    sql_assertion               = optional(string, null)
    regex                       = optional(string, null)
    allowed_values              = optional(list(string), null)

    # ── Profile-proposed flag types ────────────────────────────────────────
    non_null_expectation        = optional(bool, false)
    uniqueness_expectation      = optional(bool, false)

    # ── Object rule types ──────────────────────────────────────────────────
    range_expectation = optional(object({
      min_value          = optional(string, null)
      max_value          = optional(string, null)
      strict_min_enabled = optional(bool, false)
      strict_max_enabled = optional(bool, false)
    }), null)

    statistic_range_expectation = optional(object({
      statistic          = string
      min_value          = optional(string, null)
      max_value          = optional(string, null)
      strict_min_enabled = optional(bool, false)
      strict_max_enabled = optional(bool, false)
    }), null)
  }))
  default = []
}
