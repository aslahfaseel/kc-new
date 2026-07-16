terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.0"
    }
  }

  backend "gcs" {
    bucket = "aspect-application-poc-bucket"
    prefix = "vz/dataplex/dev" # Unique state path for Dataplex
  }
}

provider "google" {
  project                     = var.project_id
  region                      = var.region
  impersonate_service_account = var.terraform_sa
}
