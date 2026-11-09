module "private_dns" {
  source = "../modules/private-dns"

  resource_group_name = module.network.resource_group_name
  vnet_id             = module.network.vnet_id
  tags                = local.tags
}
