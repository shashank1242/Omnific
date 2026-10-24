# The OpenAI key. The secret is created EMPTY (no version); add the value out of band so it never enters state:
#   printf '%s' "$OPENAI_API_KEY" | gcloud secrets versions add omnific-openai-api-key --data-file=- --project <project>
resource "google_secret_manager_secret" "openai" {
  secret_id = "omnific-openai-api-key"
  labels    = var.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}
