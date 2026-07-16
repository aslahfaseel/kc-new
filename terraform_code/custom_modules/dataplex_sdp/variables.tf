variable "project_id" {
  description = "The GCP Project ID where resources will be deployed."
  type        = string
}

variable "location" {
  description = "The GCP region for the SDP configuration (e.g., 'us', 'europe')."
  type        = string
  default     = "us"
}

variable "inspect_template_name" {
  description = "Display name for the Sensitive Data Protection Inspect Template."
  type        = string
  default     = "Enterprise Sensitive Data Discovery Template"
}

variable "info_types" {
  type        = list(string)
  default     = [
    "EMAIL_ADDRESS",
    "CREDIT_CARD_NUMBER",
    "PERSON_NAME",
    "PHONE_NUMBER",
    "US_SOCIAL_SECURITY_NUMBER",
    "GCP_CREDENTIALS"
  ]
}

variable "min_likelihood" {
  description = "The minimum confidence level for a finding to be recorded."
  type        = string
  default     = "LIKELY"
}

variable "scan_status" {
  description = "The status of the discovery config. Can be RUNNING or PAUSED."
  type        = string
  default     = "RUNNING"
}

variable "display_name" {
  description = "The display name for the discovery configuration."
  type        = string
  default     = "Enterprise BigQuery DSP Scan"
}

variable "scan_frequency" {
  description = "How frequently data profiles can be updated when tables are modified."
  type        = string
  default     = "UPDATE_FREQUENCY_MONTHLY" 
  # Other valid options: "UPDATE_FREQUENCY_NEVER" or "UPDATE_FREQUENCY_DAILY"
}
