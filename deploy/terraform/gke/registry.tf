resource "google_artifact_registry_repository" "omnific" {
  location      = var.region
  repository_id = "omnific"
  format        = "DOCKER"
  description   = "Omnific service and sandbox-runner images"
  labels        = var.labels

  depends_on = [google_project_service.enabled]
}
