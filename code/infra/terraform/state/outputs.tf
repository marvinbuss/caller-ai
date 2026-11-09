output "state_backend" {
  description = "Non-secret coordinates of the remote state backend."
  value = {
    resource_group_name  = module.state_backend.resource_group_name
    storage_account_name = module.state_backend.storage_account_name
    container_name       = module.state_backend.container_name
    plan_container_name  = module.state_backend.plan_container_name
  }
}
