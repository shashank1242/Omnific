resource "random_id" "sql_suffix" {
  byte_length = 3
}

resource "google_sql_database_instance" "temporal" {
  # Cloud SQL names cannot be reused for about a week after deletion, hence the suffix.
  name                = "${var.cluster_name}-temporal-${random_id.sql_suffix.hex}"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.deletion_protection

  settings {
    tier                        = var.cloudsql_tier
    edition                     = "ENTERPRISE"
    availability_type           = var.cloudsql_availability_type
    disk_size                   = var.cloudsql_disk_size_gb
    disk_autoresize             = true
    user_labels                 = var.labels
    deletion_protection_enabled = var.deletion_protection

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "03:00"
    }
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "temporal" {
  name     = "temporal"
  instance = google_sql_database_instance.temporal.name
}

resource "google_sql_database" "temporal_visibility" {
  name     = "temporal_visibility"
  instance = google_sql_database_instance.temporal.name
}

resource "random_password" "temporal_db" {
  length  = 32
  special = false
}

resource "google_sql_user" "temporal" {
  name     = "temporal"
  instance = google_sql_database_instance.temporal.name
  password = random_password.temporal_db.result
}

resource "google_secret_manager_secret" "temporal_db_password" {
  secret_id = "omnific-temporal-db-password"
  labels    = var.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "temporal_db_password" {
  secret      = google_secret_manager_secret.temporal_db_password.id
  secret_data = random_password.temporal_db.result
}
