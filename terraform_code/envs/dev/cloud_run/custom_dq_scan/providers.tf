terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  backend "gcs" {
    bucket = "jyav-dev-dgsdo-0-usre-gkcenrichment"
    prefix = "vz/cloud_run/dev/custom_dq_scan"   # Unique state path
  }
}

provider "google" {
  project                     = var.project_id
  region                      = var.region
  impersonate_service_account = var.terraform_sa
}
