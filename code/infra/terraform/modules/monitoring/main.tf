locals {
  alert_description = "Owner: ${var.alerts.owner}. Runbook: ${var.alerts.runbook_url}"

  # Platform metric alerts per app. Metric names are verified on the first deployment (research R-18).
  metric_alerts = merge([
    for app, id in var.app_ids : {
      "${app}-5xx" = {
        app       = app, scope = id, metric = "Requests", aggregation = "Total", operator = "GreaterThan"
        threshold = var.alerts.http_5xx_count, dimension = true
      }
      "${app}-restarts" = {
        app       = app, scope = id, metric = "RestartCount", aggregation = "Total", operator = "GreaterThan"
        threshold = var.alerts.restart_count, dimension = false
      }
      "${app}-replicas" = {
        app       = app, scope = id, metric = "Replicas", aggregation = "Minimum", operator = "LessThan"
        threshold = 1, dimension = false
      }
      "${app}-cpu" = {
        app       = app, scope = id, metric = "CpuPercentage", aggregation = "Average", operator = "GreaterThan"
        threshold = var.alerts.cpu_percent, dimension = false
      }
      "${app}-memory" = {
        app       = app, scope = id, metric = "MemoryPercentage", aggregation = "Average", operator = "GreaterThan"
        threshold = var.alerts.memory_percent, dimension = false
      }
    }
  ]...)

  # Log-query alerts over the applications' structured events. The event fields are confirmed on the
  # first deployment (research R-18).
  query_alerts = {
    dependency-failures = {
      query     = "ContainerAppConsoleLogs_CL | where Log_s has '\"outcome\":\"failure\"' and Log_s has 'dependency' | summarize Count = count()"
      threshold = var.alerts.dependency_count
    }
    latency = {
      query     = "ContainerAppConsoleLogs_CL | where Log_s has 'latency_ms' | extend latency = todouble(extract('\"latency_ms\":([0-9.]+)', 1, Log_s)) | where latency > ${var.alerts.latency_ms} | summarize Count = count()"
      threshold = 5
    }
  }
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = var.workspace_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_days
  daily_quota_gb      = var.daily_cap_gb
  tags                = var.tags
}

resource "azurerm_application_insights" "this" {
  name                = "appi-${var.workspace_name}"
  resource_group_name = var.resource_group_name
  location            = var.location
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "web"
  tags                = var.tags
}

resource "azurerm_monitor_action_group" "this" {
  name                = var.action_group.name
  short_name          = var.action_group.short_name
  resource_group_name = var.resource_group_name
  tags                = var.tags

  dynamic "email_receiver" {
    for_each = var.action_group.email_receivers

    content {
      name          = email_receiver.key
      email_address = email_receiver.value
    }
  }
}

resource "azurerm_monitor_metric_alert" "this" {
  for_each = local.metric_alerts

  name                = "alert-${each.key}"
  resource_group_name = var.resource_group_name
  scopes              = [each.value.scope]
  description         = local.alert_description
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  tags                = var.tags

  criteria {
    metric_namespace = "Microsoft.App/containerApps"
    metric_name      = each.value.metric
    aggregation      = each.value.aggregation
    operator         = each.value.operator
    threshold        = each.value.threshold

    dynamic "dimension" {
      for_each = each.value.dimension ? [1] : []

      content {
        name     = "statusCodeCategory"
        operator = "Include"
        values   = ["5xx"]
      }
    }
  }

  action {
    action_group_id = azurerm_monitor_action_group.this.id
  }
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "this" {
  for_each = local.query_alerts

  name                 = "alert-${each.key}"
  resource_group_name  = var.resource_group_name
  location             = var.location
  scopes               = [azurerm_log_analytics_workspace.this.id]
  description          = local.alert_description
  severity             = 2
  evaluation_frequency = "PT5M"
  window_duration      = "PT15M"
  tags                 = var.tags

  criteria {
    query                   = each.value.query
    time_aggregation_method = "Total"
    metric_measure_column   = "Count"
    operator                = "GreaterThan"
    threshold               = each.value.threshold
  }

  action {
    action_groups = [azurerm_monitor_action_group.this.id]
  }
}
