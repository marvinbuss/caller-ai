output "network" {
  description = "Network identifiers that the app environment deployment reads. No value is secret."
  value = {
    resource_group_name        = module.network.resource_group_name
    vnet_id                    = module.network.vnet_id
    container_apps_subnet_id   = module.network.container_apps_subnet_id
    private_endpoint_subnet_id = module.network.private_endpoint_subnet_id
    network_security_group_id  = module.network.network_security_group_id
    route_table_id             = module.network.route_table_id
  }
}

output "private_dns_zone_ids" {
  description = "Map from purpose to private DNS zone ID."
  value       = module.private_dns.zone_ids
}
