mock_provider "azurerm" {}

variables {
  workspace_name      = "log-test"
  resource_group_name = "rg-apps"
  location            = "swedencentral"
  retention_days      = 30
  daily_cap_gb        = 1
  action_group = {
    name            = "ag-test"
    short_name      = "agtest"
    email_receivers = { operations = "operations@example.invalid" }
  }
  alerts = {
    owner          = "platform-team"
    runbook_url    = "https://example.invalid/docs/runbooks/monitoring.md"
    http_5xx_count = 5
    restart_count  = 3
    cpu_percent    = 80
    memory_percent = 80
  }
  app_ids = {
    runtime    = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-apps/providers/Microsoft.App/containerApps/ca-runtime"
    management = "/subscriptions/00000000-0000-4000-8000-000000000002/resourceGroups/rg-apps/providers/Microsoft.App/containerApps/ca-management"
  }
}

run "workspace_has_retention_and_a_daily_cap" {
  command = plan

  assert {
    condition     = azurerm_log_analytics_workspace.this.retention_in_days == 30 && azurerm_log_analytics_workspace.this.daily_quota_gb == 1
    error_message = "Retention and the daily cap must follow the configuration."
  }
}

run "platform_metric_alerts_per_app" {
  command = plan

  assert {
    condition     = length(azurerm_monitor_metric_alert.this) == 10
    error_message = "Each app needs 5xx, restart, replica, CPU and memory alerts."
  }

  assert {
    condition     = azurerm_monitor_metric_alert.this["runtime-replicas"].criteria[0].threshold == 1 && azurerm_monitor_metric_alert.this["runtime-replicas"].criteria[0].operator == "LessThan"
    error_message = "The replica alert must fire below one replica."
  }

  assert {
    condition     = azurerm_monitor_metric_alert.this["management-5xx"].criteria[0].threshold == 5 && azurerm_monitor_metric_alert.this["management-cpu"].criteria[0].threshold == 80
    error_message = "Thresholds must follow the configuration."
  }
}

run "log_query_alerts_cover_dependency_failures_and_latency" {
  command = plan

  assert {
    condition     = toset(keys(azurerm_monitor_scheduled_query_rules_alert_v2.this)) == toset(["dependency-failures", "latency"])
    error_message = "Dependency failure and latency alerts are required (Principle VII)."
  }
}

run "every_alert_carries_the_owner_and_runbook" {
  command = plan

  assert {
    condition     = alltrue([for a in azurerm_monitor_metric_alert.this : strcontains(a.description, "platform-team") && strcontains(a.description, "https://example.invalid/docs/runbooks/monitoring.md")])
    error_message = "Each alert must name its owner and runbook."
  }
}

run "alert_without_owner_is_rejected" {
  command = plan

  variables {
    alerts = {
      owner          = " "
      runbook_url    = "https://example.invalid/docs/runbooks/monitoring.md"
      http_5xx_count = 5
      restart_count  = 3
      cpu_percent    = 80
      memory_percent = 80
    }
  }

  expect_failures = [var.alerts]
}

run "alert_without_runbook_url_is_rejected" {
  command = plan

  variables {
    alerts = {
      owner          = "platform-team"
      runbook_url    = "docs/runbooks/monitoring.md"
      http_5xx_count = 5
      restart_count  = 3
      cpu_percent    = 80
      memory_percent = 80
    }
  }

  expect_failures = [var.alerts]
}

run "retention_outside_range_is_rejected" {
  command = plan

  variables {
    retention_days = 7
  }

  expect_failures = [var.retention_days]
}
