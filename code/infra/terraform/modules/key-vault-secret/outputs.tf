output "secret_id" {
  description = "Versionless Key Vault secret ID that container apps use as a secret reference. It holds no value."
  value       = "${trimsuffix(var.vault_uri, "/")}/secrets/${azapi_resource.secret.name}"
}
