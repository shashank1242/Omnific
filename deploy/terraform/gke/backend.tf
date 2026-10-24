# Local state by default. For anything shared, use a GCS backend. Create the bucket first:
#   gcloud storage buckets create gs://<project>-omnific-tfstate --location=<region> --uniform-bucket-level-access
#   gcloud storage buckets update gs://<project>-omnific-tfstate --versioning
# then uncomment and run `terraform init -migrate-state`.
#
# terraform {
#   backend "gcs" {
#     bucket = "<project>-omnific-tfstate"
#     prefix = "omnific/gke"
#   }
# }
