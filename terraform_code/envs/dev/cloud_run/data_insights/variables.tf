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

variable "image_tag" {
  description = "The commit hash tag for the container image"
  type        = string
  # No default! We want it to fail if Jenkins doesn't pass the hash.
}

