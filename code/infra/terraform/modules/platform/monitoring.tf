variable "monitoring" {
  description = "Required reviewed per-service owners, published runbooks, action-group references, thresholds and ingestion cap; no delivery approval is inferred."
  type = object({
    daily_cap_gb = number
    action_group = optional(object({
      name            = string
      short_name      = string
      email_receivers = map(string)
    }), null)
    services = map(object({
      owner                = string
      runbook_url          = string
      action_group_id      = optional(string, "")
      error_ratio          = number
      mean_latency_seconds = number
      dependency_failures  = number
      saturation_ratio     = number
      minimum_requests     = number
    }))
  })

  validation {
    condition = (
      toset(keys(var.monitoring.services)) == toset(["runtime", "management"]) &&
      var.monitoring.daily_cap_gb >= 0.1 && var.monitoring.daily_cap_gb <= 100 &&
      (var.monitoring.action_group == null || try(
        can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", var.monitoring.action_group.name)) &&
        can(regex("^[A-Za-z0-9][A-Za-z0-9_-]{0,11}$", var.monitoring.action_group.short_name)) &&
        length(var.monitoring.action_group.email_receivers) >= 1 && length(var.monitoring.action_group.email_receivers) <= 10 &&
        alltrue([for name, address in var.monitoring.action_group.email_receivers :
          can(regex("^[A-Za-z][A-Za-z0-9_-]{0,63}$", name)) &&
          length(address) <= 254 && can(regex("^[^[:space:]@]+@[^[:space:]@]+\\.[^[:space:]@]+$", address))
      ]), false)) &&
      contains([30, 60, 90, 120, 180, 270, 365, 550, 730], var.services.log_retention_days) &&
      alltrue([for service in values(var.monitoring.services) :
        can(regex("^[a-z][a-z0-9_-]{2,63}$", service.owner)) &&
        length(service.runbook_url) <= 256 && can(regex("^https://[A-Za-z0-9.-]+/[A-Za-z0-9/_.#-]+$", service.runbook_url)) &&
        (var.monitoring.action_group != null || can(regex("^/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/Microsoft.Insights/actionGroups/[A-Za-z0-9_-]+$", service.action_group_id))) &&
        service.error_ratio > 0 && service.error_ratio < 1 &&
        service.mean_latency_seconds >= 0.01 && service.mean_latency_seconds <= 10 &&
        service.dependency_failures >= 1 && service.dependency_failures <= 10000 && floor(service.dependency_failures) == service.dependency_failures &&
        service.saturation_ratio > 0 && service.saturation_ratio < 1 &&
        service.minimum_requests >= 1 && service.minimum_requests <= 10000 && floor(service.minimum_requests) == service.minimum_requests
      ])
    )
    error_message = "Require both services with safe owners/runbooks, existing group IDs or validated managed recipients, bounded thresholds and supported retention/cost limits."
  }
}

locals {
  metric_services = { runtime = "caller-runtime", management = "management" }
  action_group_ids = {
    for name, service in var.monitoring.services : name => (
      var.monitoring.action_group == null ? service.action_group_id : azurerm_monitor_action_group.application[0].id
    )
  }
  service_alerts = merge([for key, service in var.monitoring.services : {
    "${key}-errors" = {
      service   = key
      signal    = "errors"
      threshold = service.error_ratio
      query     = <<-KQL
        AppMetrics
        | where _ResourceId =~ '${azurerm_application_insights.service[key].id}'
        | where AppRoleName == '${local.metric_services[key]}' and Name in ('caller.requests', 'caller.errors')
        | summarize Requests=sumif(Sum, Name == 'caller.requests'), Errors=sumif(Sum, Name == 'caller.errors')
        | project Observed=iff(Requests >= ${service.minimum_requests}, Errors / Requests, 0.0)
      KQL
    }
    "${key}-latency" = {
      service   = key
      signal    = "latency"
      threshold = service.mean_latency_seconds
      query     = <<-KQL
        AppMetrics
        | where _ResourceId =~ '${azurerm_application_insights.service[key].id}'
        | where AppRoleName == '${local.metric_services[key]}' and Name == 'caller.request.duration'
        | summarize Duration=sum(Sum), Samples=sum(ItemCount)
        | project Observed=iff(Samples >= ${service.minimum_requests}, Duration / Samples, 0.0)
      KQL
    }
    "${key}-dependencies" = {
      service   = key
      signal    = "dependencies"
      threshold = service.dependency_failures
      query     = <<-KQL
        AppMetrics
        | where _ResourceId =~ '${azurerm_application_insights.service[key].id}'
        | where AppRoleName == '${local.metric_services[key]}' and Name == 'caller.dependency.failures'
        | summarize Observed=sum(Sum)
      KQL
    }
    "${key}-saturation" = {
      service   = key
      signal    = "saturation"
      threshold = service.saturation_ratio
      query     = <<-KQL
        AppMetrics
        | where _ResourceId =~ '${azurerm_application_insights.service[key].id}'
        | where AppRoleName == '${local.metric_services[key]}' and Name == 'caller.saturation'
        | summarize Observed=max(Max)
      KQL
    }
    "${key}-missing" = {
      service   = key
      signal    = "missing"
      threshold = 0
      query     = <<-KQL
        AppMetrics
        | where _ResourceId =~ '${azurerm_application_insights.service[key].id}'
        | where AppRoleName == '${local.metric_services[key]}' and Name == 'caller.requests'
        | summarize Samples=count()
        | project Observed=iff(Samples == 0, 1.0, 0.0)
      KQL
    }
  }]...)
}

resource "azurerm_monitor_action_group" "application" {
  count               = var.monitoring.action_group == null ? 0 : 1
  name                = var.monitoring.action_group.name
  resource_group_name = azurerm_resource_group.platform.name
  short_name          = var.monitoring.action_group.short_name
  enabled             = true
  tags                = azurerm_resource_group.platform.tags

  dynamic "email_receiver" {
    for_each = var.monitoring.action_group.email_receivers
    content {
      name                    = email_receiver.key
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }
}

output "managed_action_group_id" {
  value       = try(azurerm_monitor_action_group.application[0].id, null)
  description = "Optional managed notification destination; not proof of alert delivery."
}

resource "azurerm_application_insights" "service" {
  for_each                             = var.monitoring.services
  name                                 = "app-${var.services.monitor_name}-${each.key}"
  location                             = var.location
  resource_group_name                  = azurerm_resource_group.platform.name
  application_type                     = "other"
  workspace_id                         = azurerm_log_analytics_workspace.platform.id
  local_authentication_enabled         = false
  internet_ingestion_enabled           = false
  internet_query_enabled               = false
  ip_masking_enabled                   = true
  retention_in_days                    = var.services.log_retention_days
  daily_data_cap_in_gb                 = var.monitoring.daily_cap_gb
  daily_data_cap_notifications_enabled = true
  sampling_percentage                  = 100
  tags                                 = merge(azurerm_resource_group.platform.tags, { owner = each.value.owner, service = each.key })
}

resource "azurerm_monitor_private_link_scoped_service" "application" {
  for_each            = var.monitoring.services
  name                = "${each.key}-metrics"
  resource_group_name = azurerm_resource_group.platform.name
  scope_name          = azurerm_monitor_private_link_scope.platform.name
  linked_resource_id  = azurerm_application_insights.service[each.key].id
}

resource "azurerm_monitor_scheduled_query_rules_alert_v2" "service" {
  for_each                = local.service_alerts
  name                    = "alert-${var.services.monitor_name}-${each.key}"
  location                = var.location
  resource_group_name     = azurerm_resource_group.platform.name
  scopes                  = [azurerm_log_analytics_workspace.platform.id]
  description             = "Disabled pending qualified collection/query/delivery evidence; ${each.value.signal} for ${each.value.service}."
  enabled                 = false
  skip_query_validation   = false
  severity                = each.value.signal == "missing" ? 1 : 2
  evaluation_frequency    = "PT1M"
  window_duration         = "PT5M"
  auto_mitigation_enabled = true
  tags                    = merge(azurerm_resource_group.platform.tags, { owner = var.monitoring.services[each.value.service].owner, service = each.value.service })

  criteria {
    query                   = each.value.query
    metric_measure_column   = "Observed"
    operator                = "GreaterThan"
    threshold               = each.value.threshold
    time_aggregation_method = "Maximum"
    failing_periods {
      minimum_failing_periods_to_trigger_alert = 2
      number_of_evaluation_periods             = 2
    }
  }
  action {
    action_groups = [local.action_group_ids[each.value.service]]
    custom_properties = {
      owner   = var.monitoring.services[each.value.service].owner
      runbook = var.monitoring.services[each.value.service].runbook_url
      service = each.value.service
      signal  = each.value.signal
    }
  }
}
