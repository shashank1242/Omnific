output "cluster_name" {
  value = google_container_cluster.primary.name
}

output "cluster_endpoint" {
  value = google_container_cluster.primary.endpoint
}

output "region" {
  value = var.region
}

output "registry_url" {
  description = "Prefix for images: <registry_url>/<service>:<tag>"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.omnific.repository_id}"
}

output "artifacts_bucket" {
  value = google_storage_bucket.artifacts.name
}

output "artifacts_gsa_email" {
  description = "Annotate KSAs with iam.gke.io/gcp-service-account=<this>"
  value       = google_service_account.artifacts.email
}

output "cloudsql_private_ip" {
  value = google_sql_database_instance.temporal.private_ip_address
}

output "cloudsql_connection_name" {
  value = google_sql_database_instance.temporal.connection_name
}

output "temporal_db_password_secret" {
  description = "Secret Manager secret holding the temporal DB password."
  value       = google_secret_manager_secret.temporal_db_password.secret_id
}

output "openai_secret" {
  value = google_secret_manager_secret.openai.secret_id
}

output "get_credentials_command" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.primary.name} --region ${var.region} --project ${var.project_id}"
}
