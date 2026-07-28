locals {
  bq_prefix             = "//bigquery.googleapis.com/projects/${var.project_id}"
  dq_results_table      = "${local.bq_prefix}/datasets/gkc_governance_tbls/tables/data_quality_results"
  profile_results_table = "${local.bq_prefix}/datasets/gkc_governance_tbls/tables/data_profiling_results"
  common_labels = {
    project     = "vz"
    environment = "dev"
    managed_by  = "terraform"
  }
}

module "aspect_type_asset_governance" {
  source         = "../../../custom_modules/dataplex_aspect_type"
  project_id     = var.project_id
  location       = "us"
  aspect_type_id = "data-governance"
  display_name   = "Data Governance"
  description    = "Governance metadata: owner, domain, and lifecycle stage of the data asset"
  labels         = local.common_labels

  metadata_template = jsonencode({
    name         = "data-governance"
    type         = "record"
    recordFields = [
      {
        name        = "data_owner"
        type        = "string"
        index       = 1
        annotations = { displayName = "Data Owner", description = " Business / Functional lead that manages the data" }
        constraints = { required = false }
      },
      {
        name        = "data_domain"
        type        = "string"
        index       = 2
        annotations = { displayName = "Data Domain", description = " Sample values = Accessory Sales, Accounts Payable & Accounts Receivable" }       
        constraints = { required = false }
      },
      {
        name        = "data_domain_description"
        type        = "string"
        index       = 3
        annotations = { displayName = "Data Domain Description", description = "Description" }       
        constraints = { required = false }
      },
      {
        name        = "data_lifecycle"
        type        = "enum"
        index       = 4
        annotations = { displayName = "Data Lifecycle", description = "Indication of the data layer for the attached object" }
        enumValues = [
          { name = "Landing Zone Bronze Layer", index = 1 },
          { name = "Processing Zone Silver Layer", index = 2 },
          { name = "Curated Zone Gold Layer", index = 3 }
        ]
        constraints = { required = false }
      }
    ]
  })
}

module "aspect_type_data_trustability" {
  source         = "../../../custom_modules/dataplex_aspect_type"
  project_id     = var.project_id
  location       = "us"
  aspect_type_id = "data-trustability"
  display_name   = "Data Trustability"
  description    = "Aspect type for overall tracking of automated and manual data trust levels"
  labels         = local.common_labels

  metadata_template = jsonencode({
    name         = "data-trustability"
    type         = "record"
    recordFields = [
      {
        name        = "trust_score"
        type        = "enum"
        index       = 1
        annotations = {
          displayName = "Trust Score"
          description = "The overall trust tier based on domain evaluation rule outcomes"
        }
        constraints = { required = false }
        enumValues = [
          { name = "high",    index = 1 },
          { name = "medium",  index = 2 },
          { name = "low",     index = 3 },
          { name = "unknown", index = 4 }
        ]
      },
      {
        name        = "last_evaluated"
        type        = "datetime"
        index       = 2
        annotations = {
          displayName = "Last Evaluated"
          description = "The exact timestamp when the data quality scan was executed."
        }
        constraints = { required = false }
      }
    ]
  })
}

data "google_storage_bucket_object_content" "profile_dq_csv" {
  name   = "profile_based_dq.csv"
  bucket = var.gcs_bucket_name
}

locals {
  profile_dq_raw = csvdecode(data.google_storage_bucket_object_content.profile_dq_csv.content)

  profile_based_scans = {
    for row in local.profile_dq_raw :
    row.table_name => {
      project_id               = row.project_id
      region                   = lookup(row, "location", var.location)
      dataset_id               = row.dataset
      table_id                 = row.table_name
      # Dynamically constructed scan ID matching your naming pattern: dp-<dataset>-<table>-test
      existing_profile_scan_id = "dp-${replace(row.dataset, "_", "-")}-${replace(row.table_name, "_", "-")}-test"
    }
  }
}

data "google_client_config" "default" {}

data "http" "profile_scan_details" {
  for_each = local.profile_based_scans
  url      = "https://dataplex.googleapis.com/v1/projects/${var.project_id}/locations/${each.value.region}/dataScans/${each.value.existing_profile_scan_id}"
  request_headers = {
    Authorization = "Bearer ${data.google_client_config.default.access_token}"
    Accept        = "application/json"
  }
}

locals {
  profile_target_resources = {
    for key, response in data.http.profile_scan_details :
    key => try(jsondecode(response.response_body).data.resource, jsondecode(response.response_body).resource, "")
  }
}

data "google_dataplex_data_quality_rules" "recommendations" {
  for_each     = local.profile_based_scans
  project      = var.project_id
  location     = each.value.region
  data_scan_id = each.value.existing_profile_scan_id
}

resource "google_dataplex_datascan" "dq_from_profile" {
  for_each     = local.profile_based_scans
  project      = var.project_id
  location     = each.value.region
  data_scan_id = "${replace(each.value.dataset_id, "_", "-")}-${replace(each.value.table_id, "_", "-")}-profile-based"
  display_name = "${each.value.project_id} - ${each.value.dataset_id} - ${title(replace(each.value.table_id, "_", " "))} - profile based dq scan"
  labels       = merge(local.common_labels, { scan_type = "dq-profile-based" })

  data {
    resource = local.profile_target_resources[each.key]
  }

  execution_spec {
    trigger {
      on_demand {}
    }
  }

  execution_identity {
    service_account {
      email = var.terraform_sa
    }
  }

  data_quality_spec {
    catalog_publishing_enabled = true

    dynamic "rules" {
      for_each = data.google_dataplex_data_quality_rules.recommendations[each.key].rules
      content {
        column      = rules.value.column
        dimension   = rules.value.dimension
        threshold   = rules.value.threshold
        ignore_null = rules.value.ignore_null
        name        = rules.value.name
        description = rules.value.description

        dynamic "non_null_expectation" {
          for_each = rules.value.non_null_expectation
          content {}
        }
        dynamic "uniqueness_expectation" {
          for_each = rules.value.uniqueness_expectation
          content {}
        }
        dynamic "range_expectation" {
          for_each = rules.value.range_expectation
          content {
            min_value          = range_expectation.value.min_value
            max_value          = range_expectation.value.max_value
            strict_min_enabled = range_expectation.value.strict_min_enabled
            strict_max_enabled = range_expectation.value.strict_max_enabled
          }
        }
        dynamic "regex_expectation" {
          for_each = rules.value.regex_expectation
          content {
            regex = regex_expectation.value.regex
          }
        }
        dynamic "set_expectation" {
          for_each = rules.value.set_expectation
          content {
            values = set_expectation.value.values
          }
        }
        dynamic "statistic_range_expectation" {
          for_each = rules.value.statistic_range_expectation
          content {
            statistic          = statistic_range_expectation.value.statistic
            min_value          = statistic_range_expectation.value.min_value
            max_value          = statistic_range_expectation.value.max_value
            strict_min_enabled = statistic_range_expectation.value.strict_min_enabled
            strict_max_enabled = statistic_range_expectation.value.strict_max_enabled
          }
        }
        dynamic "row_condition_expectation" {
          for_each = rules.value.row_condition_expectation
          content {
            sql_expression = row_condition_expectation.value.sql_expression
          }
        }
        dynamic "table_condition_expectation" {
          for_each = rules.value.table_condition_expectation
          content {
            sql_expression = table_condition_expectation.value.sql_expression
          }
        }
        dynamic "sql_assertion" {
          for_each = rules.value.sql_assertion
          content {
            sql_statement = sql_assertion.value.sql_statement
          }
        }
      }
    }

    post_scan_actions {
      bigquery_export {
        results_table = local.dq_results_table
      }
    }
  }
}
