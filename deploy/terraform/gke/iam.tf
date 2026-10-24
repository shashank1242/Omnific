locals {
  # service -> namespace for the artifacts GSA. KSA name is always omnific-<service>.
  artifact_ksas = {
    api       = "omnific-system"
    sandbox   = "omnific-sandbox"
    data      = "omnific-data"
    mesh      = "omnific-mesh"
    evaluator = "omnific-evaluator"
  }

  # service -> namespace for the OpenAI secret reader GSA.
  secret_reader_ksas = {
    researcher   = "omnific-researcher"
    modeler      = "omnific-modeler"
    solver-coder = "omnific-solver-coder"
    evaluator    = "omnific-evaluator"
  }
}

# --- artifacts bucket access ---
resource "google_service_account" "artifacts" {
  account_id   = "omnific-artifacts"
  display_name = "Omnific artifacts (GCS) via Workload Identity"

  depends_on = [google_project_service.enabled]
}

resource "google_storage_bucket_iam_member" "artifacts_admin" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.artifacts.email}"
}

resource "google_service_account_iam_member" "artifacts_wi" {
  for_each = local.artifact_ksas

  service_account_id = google_service_account.artifacts.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value}/omnific-${each.key}]"

  depends_on = [google_container_cluster.primary]
}

# --- OpenAI key access (GKE Secret Manager add-on mounts it into the pods) ---
# Granted directly to each KSA's Workload Identity principal, with no intermediate GSA. A KSA can impersonate only one
# GSA (one iam.gke.io/gcp-service-account annotation), and the evaluator already needs the artifacts GSA.
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_secret_manager_secret_iam_member" "openai_accessor" {
  for_each = local.secret_reader_ksas

  secret_id = google_secret_manager_secret.openai.id
  role      = "roles/secretmanager.secretAccessor"
  member = join("/", [
    "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global",
    "workloadIdentityPools/${var.project_id}.svc.id.goog/subject/ns/${each.value}/sa/omnific-${each.key}",
  ])

  depends_on = [google_container_cluster.primary]
}
