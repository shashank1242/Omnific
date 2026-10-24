# omnific Helm chart

Deploys every Omnific service (api, web, workflows, researcher, modeler, solver-coder, data, mesh, evaluator, sandbox)
as its own Deployment in its own namespace, plus the `omnific-sandbox-runs` namespace for gVisor runner Jobs.
Follows `docs/deployment.md`. Services are driven by the `services:` map in `values.yaml`.

## What is rendered
- Namespaces (restricted PodSecurity), ServiceAccounts `omnific-<svc>` (Workload Identity annotation when `needsArtifacts`)
- Deployment per service: non-root, read-only rootfs + `/tmp` emptyDir, drop ALL caps, seccomp RuntimeDefault,
  `nodeSelector omnific.io/pool=general`; Service for api/web; HPA (api/web on by default); PDB when replicas > 1
- NetworkPolicies: per-namespace default-deny ingress; per-service egress (DNS, Temporal 7233, 443 only for
  OpenAI/GCS callers, metadata server for Workload Identity); web ingress from GCLB ranges; api ingress from web
- Sandbox: env `SANDBOX_BACKEND=k8s`, `SANDBOX_NAMESPACE`, `SANDBOX_IMAGE`, `SANDBOX_RUNTIME_CLASS=gvisor`;
  Role/RoleBinding in `omnific-sandbox-runs` (jobs, configmaps: create/get/list/watch/delete; pods, pods/log: get/list/watch);
  deny-all NetworkPolicy, ResourceQuota, LimitRange there
- GKE Ingress for `web`, BackendConfig health check; ManagedCertificate + FrontendConfig (HTTPS redirect) when `ingress.host` is set

## Install
```bash
helm upgrade --install omnific deploy/helm/omnific -f deploy/helm/omnific/values-gke.yaml \
  --set global.project=<id> --set global.imageTag=<tag>
```
Install Temporal first (see below). `global.project` defaults to the placeholder `PROJECT_ID`.
If Terraform already creates the namespaces, set `namespaces.create=false`.

## OpenAI secret
`omnific-openai` (key `OPENAI_API_KEY`) must exist in researcher, modeler, solver-coder and evaluator namespaces.
- `secrets.provider=manual` (default): create it per namespace:
  ```bash
  for ns in omnific-researcher omnific-modeler omnific-solver-coder omnific-evaluator; do
    kubectl -n $ns create secret generic omnific-openai \
      --from-literal=OPENAI_API_KEY="$(gcloud secrets versions access latest --secret=omnific-openai-api-key)"
  done
  ```
  Pods fail with CreateContainerConfigError until it exists.
- `secrets.provider=gke-secret-manager`: renders a `SecretProviderClass` (`omnific-openai`) per OpenAI namespace and mounts
  it via the CSI driver `secrets-store-gke.csi.k8s.io`, with `secretObjects` to sync the K8s Secret. Requires the add-on and
  `roles/secretmanager.secretAccessor` for the pod's workload identity principal. Caveat: verify your add-on version
  supports `secretObjects` sync; if not, use `manual`.

## Images
`<region>-docker.pkg.dev/<project>/omnific/<image>:<imageTag>`; runner `.../sandbox-runner:<imageTag>`.
The Python Dockerfiles currently run as root; the chart forces `runAsUser` (default 10001), which works because `/app` is
world-readable and HOME/UV_CACHE_DIR point to `/tmp`. Adding a `USER` to the Dockerfiles is recommended.

## Temporal
```bash
helm repo add temporal https://go.temporal.io/helm-charts && helm repo update
helm upgrade --install temporal temporal/temporal --version 1.6.0 -n temporal --create-namespace \
  -f deploy/helm/temporal-values.yaml
```
Chart repo: https://go.temporal.io/helm-charts. Every key in `temporal-values.yaml` was checked against
`helm show values temporal/temporal --version 1.6.0`, and the file renders with `helm template`. Re-check the keys before
bumping the chart version. Create the `temporal-db` secret and set `connectAddr` to the Cloud SQL private IP first.

## Validate
`helm lint deploy/helm/omnific -f deploy/helm/omnific/values-gke.yaml` and `helm template ... | kubeconform -ignore-missing-schemas -`
(BackendConfig, FrontendConfig, ManagedCertificate, SecretProviderClass are CRDs).
