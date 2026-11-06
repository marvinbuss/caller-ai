# Both approved secrets reach the vault over the management API (FR-046, FR-051).
module "signin_secret" {
  source = "../modules/key-vault-secret"

  name      = "management-signin-secret"
  vault_id  = module.secret_store.id
  vault_uri = module.secret_store.vault_uri
  value     = var.management_signin_secret
}

module "permit_private_key_secret" {
  source = "../modules/key-vault-secret"

  name      = "permit-private-key"
  vault_id  = module.secret_store.id
  vault_uri = module.secret_store.vault_uri
  value     = module.permit_key.private_key_pem
}
