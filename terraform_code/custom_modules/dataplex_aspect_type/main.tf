resource "google_dataplex_aspect_type" "this" {
  project           = var.project_id
  location          = var.location
  aspect_type_id    = var.aspect_type_id
  display_name      = var.display_name
  description       = var.description
  labels            = var.labels
  metadata_template = var.metadata_template
}
