# Deployment (GKE)

One GKE Standard cluster. Every Omnific service is its own Deployment in its own namespace. Generated solver
code runs as short-lived Jobs on a GKE Sandbox (gVisor) node pool with no network.

## Contract between Terraform, Helm and the code
| Thing | Value |
|---|---|
| Node pools (label `omnific.io/pool`) | `general` (all services, Temporal); `sandbox` (GKE Sandbox gVisor; GKE adds taint `sandbox.gke.io/runtime=gvisor:NoSchedule`); `gpu` (optional, scale-to-zero, taint `nvidia.com/gpu=present:NoSchedule`, for the future Mesh v2) |
| Namespaces | `omnific-system` (api, web, workflows); `omnific-<agent>` for researcher, modeler, solver-coder, data, mesh, evaluator, sandbox; `omnific-sandbox-runs` (runner Jobs only); `temporal` |
| Kubernetes SAs | `omnific-<service>` in that service's namespace |
| Google SA | `omnific-artifacts@<project>.iam.gserviceaccount.com` (roles/storage.objectAdmin on the artifacts bucket), bound through Workload Identity to the KSAs of: api, sandbox, data, mesh, evaluator |
| Artifacts | GCS bucket `<project>-omnific-artifacts`; env `ARTIFACT_STORE=gcs`, `GCS_BUCKET` |
| Images | Artifact Registry `<region>-docker.pkg.dev/<project>/omnific/<service>:<tag>`; runner image `.../sandbox-runner:<tag>` |
| OpenAI key | Secret Manager secret `omnific-openai-api-key`, synced into the namespaces that call OpenAI (researcher, modeler, solver-coder, evaluator) as K8s Secret `omnific-openai` with key `OPENAI_API_KEY` |
| Temporal | Self-hosted with the official `temporalio/temporal` Helm chart in namespace `temporal`, persisting to Cloud SQL Postgres (private IP). Address `temporal-frontend.temporal.svc:7233`. Temporal Cloud is a drop-in alternative (set `TEMPORAL_ADDRESS` plus mTLS) |
| Sandbox backend | `SANDBOX_BACKEND=k8s`, `SANDBOX_NAMESPACE=omnific-sandbox-runs`, `SANDBOX_IMAGE`, `SANDBOX_RUNTIME_CLASS=gvisor`. The sandbox worker's KSA has a Role in `omnific-sandbox-runs` to create, get, list, watch and delete `jobs` and `configmaps`, and to get, list and watch `pods` and `pods/log`. Nothing else |
| Runner Jobs | `runtimeClassName: gvisor`, nodeSelector `omnific.io/pool: sandbox`, `automountServiceAccountToken: false`, non-root, read-only rootfs, drop ALL caps, CPU/memory limits, `activeDeadlineSeconds`, `ttlSecondsAfterFinished`. A deny-all NetworkPolicy (ingress and egress) covers the namespace |
| Ingress | GKE Ingress → `web` (/) and `api` (/api is proxied by web, so the API needn't be public) |

## Sandbox data path on Kubernetes
The runner has no network and no token, so I/O goes through the Kubernetes API. The worker writes spec.json, solver.py and refine
into a ConfigMap (1 MiB limit, so solver code is capped). The Job mounts it read-only at /work/in. The harness writes the
result npz to a tmpfs and prints it base64-encoded between `-----OMNIFIC-RESULT-BEGIN-----` and `-----END-----`
markers on stdout. The worker reads the pod log, decodes it and uploads the result to GCS. The result is capped at 6 MiB, so its base64 (about 8 MiB) stays under the kubelet default 10 MiB log rotation.

## Apply (manual, never from CI on PRs)
```bash
cd deploy/terraform/gke
terraform init && terraform plan -var project_id=<id> -var region=<region>
terraform apply ...                    # creates billable resources
gcloud container clusters get-credentials omnific --region <region>
helm repo add temporal https://go.temporal.io/helm-charts
helm upgrade --install temporal temporal/temporal --version 1.6.0 -n temporal --create-namespace \
  -f deploy/helm/temporal-values.yaml   # set connectAddr to the Cloud SQL private IP (terraform output)
helm upgrade --install omnific deploy/helm/omnific -f deploy/helm/omnific/values-gke.yaml \
  --set global.project=<id> --set global.imageTag=<tag>
```
