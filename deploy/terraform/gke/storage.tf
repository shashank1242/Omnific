resource "google_storage_bucket" "artifacts" {
  name     = "${var.project_id}-omnific-artifacts"
  location = var.region
  labels   = var.labels

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.artifacts_force_destroy

  versioning {
    enabled = false
  }

  dynamic "lifecycle_rule" {
    for_each = var.artifact_retention_days > 0 ? [1] : []
    content {
      condition {
        age = var.artifact_retention_days
      }
      action {
        type = "Delete"
      }
    }
  }

  depends_on = [google_project_service.enabled]
}
