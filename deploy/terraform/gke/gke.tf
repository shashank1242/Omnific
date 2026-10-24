# Least-privilege node service account (not the default Compute SA).
resource "google_service_account" "nodes" {
  account_id   = "${var.cluster_name}-nodes"
  display_name = "Omnific GKE nodes"

  depends_on = [google_project_service.enabled]
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/artifactregistry.reader",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "primary" {
  name     = var.cluster_name
  location = var.region

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.nodes.id

  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Dataplane V2 (Cilium): enforces NetworkPolicy (the sandbox namespace relies on deny-all).
  datapath_provider = "ADVANCED_DATAPATH"

  remove_default_node_pool = true
  initial_node_count       = 1
  node_config {
    service_account = google_service_account.nodes.email
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = var.master_ipv4_cidr
  }

  dynamic "master_authorized_networks_config" {
    for_each = length(var.master_authorized_networks) > 0 ? [1] : []
    content {
      dynamic "cidr_blocks" {
        for_each = var.master_authorized_networks
        content {
          cidr_block   = cidr_blocks.value.cidr_block
          display_name = cidr_blocks.value.display_name
        }
      }
    }
  }

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  enable_shielded_nodes = true

  # GKE Secret Manager add-on (managed Secret Manager CSI driver).
  secret_manager_config {
    enabled = true
  }

  resource_labels     = var.labels
  deletion_protection = var.deletion_protection

  depends_on = [google_project_iam_member.nodes]
}

locals {
  node_zones     = length(var.node_zones) > 0 ? var.node_zones : null
  gpu_node_zones = length(var.gpu_node_zones) > 0 ? var.gpu_node_zones : local.node_zones
}

resource "google_container_node_pool" "general" {
  name           = "general"
  cluster        = google_container_cluster.primary.id
  node_locations = local.node_zones

  autoscaling {
    total_min_node_count = var.general_min_nodes
    total_max_node_count = var.general_max_nodes
    location_policy      = "BALANCED"
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.general_machine_type
    image_type      = "COS_CONTAINERD"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels          = { "omnific.io/pool" = "general" }
    resource_labels = var.labels

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
}

# GKE Sandbox (gVisor). GKE automatically adds the taint sandbox.gke.io/runtime=gvisor:NoSchedule.
resource "google_container_node_pool" "sandbox" {
  name           = "sandbox"
  cluster        = google_container_cluster.primary.id
  node_locations = local.node_zones

  autoscaling {
    total_min_node_count = var.sandbox_min_nodes
    total_max_node_count = var.sandbox_max_nodes
    location_policy      = "BALANCED"
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.sandbox_machine_type
    image_type      = "COS_CONTAINERD"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels          = { "omnific.io/pool" = "sandbox" }
    resource_labels = var.labels

    sandbox_config {
      type = "GVISOR"
    }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
}

resource "google_container_node_pool" "gpu" {
  count = var.enable_gpu_pool ? 1 : 0

  name           = "gpu"
  cluster        = google_container_cluster.primary.id
  node_locations = local.gpu_node_zones

  autoscaling {
    total_min_node_count = var.gpu_min_nodes
    total_max_node_count = var.gpu_max_nodes
    location_policy      = "ANY"
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.gpu_machine_type
    image_type      = "COS_CONTAINERD"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels          = { "omnific.io/pool" = "gpu" }
    resource_labels = var.labels

    guest_accelerator {
      type  = var.gpu_accelerator_type
      count = var.gpu_accelerator_count
      gpu_driver_installation_config {
        gpu_driver_version = "LATEST"
      }
    }

    taint {
      key    = "nvidia.com/gpu"
      value  = "present"
      effect = "NO_SCHEDULE"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
}
