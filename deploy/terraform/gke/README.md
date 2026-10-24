# Omnific on GKE (Terraform)

Creates the infrastructure described in `docs/deployment.md`: a private regional GKE Standard cluster
(pools `general`, `sandbox` with gVisor, optional `gpu`), VPC + Cloud NAT, Artifact Registry `omnific`,
the artifacts bucket, Workload Identity service accounts, Secret Manager secrets and a private Cloud SQL
Postgres 16 for Temporal.

## Prerequisites
- Terraform >= 1.6, `gcloud` authenticated (`gcloud auth application-default login`).
- A GCP project with billing enabled. Your principal needs Owner, or Editor plus Project IAM Admin and
  Service Networking Admin.

## Remote state
State is local by default. See the commented GCS backend in `backend.tf`. The Cloud SQL password lands in
state (random_password), so keep state in a private bucket.

## Apply
```bash
cd deploy/terraform/gke
terraform init
terraform plan  -var project_id=<id> -var region=us-central1 -out tfplan
terraform apply tfplan                       # creates billable resources
gcloud container clusters get-credentials omnific --region us-central1 --project <id>   # also an output
```
Set `master_authorized_networks` (list of `{cidr_block, display_name}`) to restrict control plane access.
Enable GPUs with `-var enable_gpu_pool=true` (check L4 availability in your zones; use `gpu_node_zones`).
Autoscaling bounds are totals across zones (`total_min_node_count` / `total_max_node_count`).

### Add the OpenAI key (not managed by Terraform)
```bash
printf '%s' "$OPENAI_API_KEY" | gcloud secrets versions add omnific-openai-api-key --data-file=- --project <id>
```

### Secrets in the cluster
The GKE Secret Manager add-on is enabled (`secret_manager_config`) and mounts the key through a CSI
`SecretProviderClass` (Helm `secrets.provider=gke-secret-manager`). Access to the `omnific-openai-api-key` secret is
granted **directly** to the Workload Identity principals of the KSAs `omnific-researcher`, `omnific-modeler`,
`omnific-solver-coder` and `omnific-evaluator`, so those KSAs need no annotation for it. Only the KSAs of api,
sandbox, data, mesh and evaluator are annotated with `iam.gke.io/gcp-service-account=<artifacts_gsa_email output>`.
That lets the evaluator use both without impersonating two GSAs.

### Temporal DB
Host: `cloudsql_private_ip` output, port 5432, databases `temporal` and `temporal_visibility`, user `temporal`,
password in Secret Manager `omnific-temporal-db-password`.

## Cost notes (rough)
Billable while up: GKE cluster management fee (one free cluster credit per billing account), the `general`
pool (min 1 x e2-standard-4), Cloud SQL (db-custom-2-7680 plus disk and backups, roughly 100+ USD/month),
Cloud NAT gateway and egress, GCS and Artifact Registry storage, Secret Manager (cents). The sandbox pool
scales to zero when idle. The GPU pool exists only with `enable_gpu_pool=true` and scales to zero.

## Teardown
```bash
terraform apply   -var project_id=<id> -var deletion_protection=false   # lift protection first
terraform destroy -var project_id=<id> -var deletion_protection=false
```
Set `artifacts_force_destroy=true` as well if the bucket holds objects. APIs stay enabled
(`disable_on_destroy = false`).
