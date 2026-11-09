module "state_backend" {
  source = "../modules/state-backend"

  resource_group_name  = var.state.resource_group_name
  location             = var.environment.location
  storage_account_name = var.state.storage_account_name
  state_retention_days = var.state.state_retention_days
  writer_principal_ids = var.state.writer_principal_ids
  tags                 = local.tags
}
