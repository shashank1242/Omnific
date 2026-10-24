variable "project_id" {
  description = "GCP project ID that hosts everything."
  type        = string
}

variable "region" {
  description = "Region for the regional GKE cluster, registry, bucket and Cloud SQL."
  type        = string
  default     = "us-central1"
}

variable "node_zones" {
  description = "Optional list of zones for node pools (e.g. [\"us-central1-a\",\"us-central1-b\"]). Empty = GKE default zones of the region."
  type        = list(string)
  default     = []
}

variable "gpu_node_zones" {
  description = "Optional zones for the GPU pool only (GPU types are not in every zone). Empty = use node_zones."
  type        = list(string)
  default     = []
}

variable "cluster_name" {
  description = "GKE cluster name."
  type        = string
  default     = "omnific"
}

variable "labels" {
  description = "Labels applied to GCP resources that support them."
  type        = map(string)
  default = {
    app = "omnific"
  }
}

variable "deletion_protection" {
  description = "Protect the cluster and the Cloud SQL instance from deletion. Set false before `terraform destroy`."
  type        = bool
  default     = true
}

variable "master_authorized_networks" {
  description = "CIDRs allowed to reach the GKE control plane endpoint. Empty list = no authorized networks block (endpoint reachable from the internet, still IAM-authenticated). Set this in real use."
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

# --- network ---
variable "subnet_cidr" {
  description = "Primary range of the node subnet."
  type        = string
  default     = "10.10.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range for pods."
  type        = string
  default     = "10.20.0.0/14"
}

variable "services_cidr" {
  description = "Secondary range for services."
  type        = string
  default     = "10.30.0.0/20"
}

variable "master_ipv4_cidr" {
  description = "/28 for the private control plane peering."
  type        = string
  default     = "172.16.0.0/28"
}

# --- node pools ---
variable "general_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "general_min_nodes" {
  description = "Total (across zones) minimum for the general pool."
  type        = number
  default     = 1
}

variable "general_max_nodes" {
  type    = number
  default = 6
}

variable "sandbox_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "sandbox_min_nodes" {
  type    = number
  default = 0
}

variable "sandbox_max_nodes" {
  type    = number
  default = 4
}

variable "enable_gpu_pool" {
  description = "Create the optional GPU node pool (scale to zero)."
  type        = bool
  default     = false
}

variable "gpu_machine_type" {
  type    = string
  default = "g2-standard-8"
}

variable "gpu_accelerator_type" {
  type    = string
  default = "nvidia-l4"
}

variable "gpu_accelerator_count" {
  description = "GPUs per node. Must match the machine type (g2-standard-8 has 1 L4)."
  type        = number
  default     = 1
}

variable "gpu_min_nodes" {
  type    = number
  default = 0
}

variable "gpu_max_nodes" {
  type    = number
  default = 2
}

# --- storage ---
variable "artifact_retention_days" {
  description = "Delete artifact objects older than this many days. 0 disables the lifecycle rule."
  type        = number
  default     = 90
}

variable "artifacts_force_destroy" {
  description = "Allow terraform to delete the artifacts bucket even if it has objects."
  type        = bool
  default     = false
}

# --- Cloud SQL ---
variable "cloudsql_tier" {
  type    = string
  default = "db-custom-2-7680"
}

variable "cloudsql_disk_size_gb" {
  type    = number
  default = 20
}

variable "cloudsql_availability_type" {
  description = "ZONAL or REGIONAL (HA, roughly doubles cost)."
  type        = string
  default     = "ZONAL"
}
