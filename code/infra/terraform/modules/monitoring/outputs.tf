output "workspace_id" {
  description = "Resource ID of the Log Analytics workspace."
  value       = azurerm_log_analytics_workspace.this.id
}

output "workspace_name" {
  description = "Name of the Log Analytics workspace."
  value       = azurerm_log_analytics_workspace.this.name
}

output "application_insights_id" {
  description = "Resource ID of the workspace-based Application Insights component."
  value       = azurerm_application_insights.this.id
}

output "action_group_id" {
  description = "Resource ID of the action group that receives every alert."
  value       = azurerm_monitor_action_group.this.id
}

output "alert_names" {
  description = "Names of the metric and log-query alerts."
  value = concat(
    [for a in azurerm_monitor_metric_alert.this : a.name],
    [for a in azurerm_monitor_scheduled_query_rules_alert_v2.this : a.name],
  )
}
