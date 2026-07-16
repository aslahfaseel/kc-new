resource "google_dataplex_datascan" "quality" {
  project      = var.project_id
  location     = var.location
  data_scan_id = var.data_scan_id
  display_name = var.display_name
  description  = var.description
  labels       = var.labels

  data {
    resource = var.source_bq_table
  }

  dynamic "execution_identity" {
    for_each = var.service_account_email != null ? [1] : []
    content {
      service_account {
        email = var.service_account_email
      }
    }
  }

  execution_spec {
    trigger {
      dynamic "on_demand" {
        for_each = var.schedule_cron == null ? [1] : []
        content {}
      }
      dynamic "schedule" {
        for_each = var.schedule_cron != null ? [1] : []
        content {
          cron = var.schedule_cron
        }
      }
    }
  }

  data_quality_spec {
    sampling_percent           = var.sampling_percent
    row_filter                 = var.row_filter
    catalog_publishing_enabled = true

    dynamic "rules" {
      for_each = var.dq_rules
      content {
        name        = rules.value.name
        description = lookup(rules.value, "description", "")
        dimension   = rules.value.dimension 
        threshold = (
          lookup(rules.value, "table_condition_sql", null) != null || 
          lookup(rules.value, "sql_assertion", null) != null
        ) ? null : lookup(rules.value, "threshold", 1.0)
        column      = lookup(rules.value, "column", null)
        ignore_null = lookup(rules.value, "ignore_null", null)

        dynamic "non_null_expectation" {
          for_each = lookup(rules.value, "non_null_expectation", false) ? [1] : []
          content {}
        }

        dynamic "uniqueness_expectation" {
          for_each = lookup(rules.value, "uniqueness_expectation", false) ? [1] : []
          content {}
        }

        dynamic "range_expectation" {
          for_each = lookup(rules.value, "range_expectation", null) != null ? [rules.value.range_expectation] : []
          content {
            min_value          = lookup(range_expectation.value, "min_value", null)
            max_value          = lookup(range_expectation.value, "max_value", null)
            strict_min_enabled = lookup(range_expectation.value, "strict_min_enabled", false)
            strict_max_enabled = lookup(range_expectation.value, "strict_max_enabled", false)
          }
        }

        dynamic "regex_expectation" {
          for_each = lookup(rules.value, "regex", null) != null ? [1] : []
          content {
            regex = rules.value.regex
          }
        }

        dynamic "set_expectation" {
          for_each = lookup(rules.value, "allowed_values", null) != null ? [1] : []
          content {
            values = rules.value.allowed_values
          }
        }

        dynamic "statistic_range_expectation" {
          for_each = lookup(rules.value, "statistic_range_expectation", null) != null ? [rules.value.statistic_range_expectation] : []
          content {
            statistic          = statistic_range_expectation.value.statistic
            min_value          = lookup(statistic_range_expectation.value, "min_value", null)
            max_value          = lookup(statistic_range_expectation.value, "max_value", null)
            strict_min_enabled = lookup(statistic_range_expectation.value, "strict_min_enabled", false)
            strict_max_enabled = lookup(statistic_range_expectation.value, "strict_max_enabled", false)
          }
        }

        dynamic "row_condition_expectation" {
          for_each = lookup(rules.value, "row_condition_sql", null) != null ? [1] : []
          content {
            sql_expression = rules.value.row_condition_sql
          }
        }

        dynamic "table_condition_expectation" {
          for_each = lookup(rules.value, "table_condition_sql", null) != null ? [1] : []
          content {
            sql_expression = rules.value.table_condition_sql
          }
        }

        dynamic "sql_assertion" {
          for_each = lookup(rules.value, "sql_assertion", null) != null ? [1] : []
          content {
            sql_statement = rules.value.sql_assertion
          }
        }
      }
    }

    post_scan_actions {
      bigquery_export {
        results_table = var.results_bq_table
      }
    }
  }
}
#
