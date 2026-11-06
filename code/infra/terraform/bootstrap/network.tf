module "network" {
  source = "../modules/network"

  resource_group_name          = var.bootstrap.resource_group_name
  location                     = var.environment.location
  vnet_name                    = var.bootstrap.vnet_name
  address_space                = var.bootstrap.address_space
  container_apps_subnet_cidr   = var.bootstrap.container_apps_subnet_cidr
  private_endpoint_subnet_cidr = var.bootstrap.private_endpoint_subnet_cidr
  tags                         = local.tags
}
