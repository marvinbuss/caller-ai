output "zone_ids" {
  description = "Map from purpose (cosmos, vault, openai, cognitive, ai_services, monitor, oms, ods, agentsvc, blob) to private DNS zone ID."
  value       = { for purpose, zone in azurerm_private_dns_zone.this : purpose => zone.id }
}
