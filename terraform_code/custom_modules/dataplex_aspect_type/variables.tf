variable "project_id" {
  type = string
}

variable "location" {
  type    = string
  default = "us-central1"
}

variable "aspect_type_id" {
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

variable "metadata_template" {
  type = string
}

