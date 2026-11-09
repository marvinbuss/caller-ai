output "key_id" {
  description = "Key ID of the permit signing key pair."
  value       = var.key_id
}

output "public_key_pem" {
  description = "Public key in PEM format. The runtime receives it as a setting. It is not a secret."
  value       = tls_private_key.permit.public_key_pem
}

output "private_key_pem" {
  description = "Private key in PEM format. Only the key-vault-secret module may use it."
  value       = tls_private_key.permit.private_key_pem
  sensitive   = true
}
