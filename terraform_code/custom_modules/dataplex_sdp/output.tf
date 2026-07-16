output "discovery_config_id" {
  description = "The ID of the deployed SDP Discovery Configuration."
  value       = google_data_loss_prevention_discovery_config.bq_discovery.id
}
