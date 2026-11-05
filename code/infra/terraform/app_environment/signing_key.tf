# The permit key pair is generated here. The private key goes to the vault only (FR-051).
module "permit_key" {
  source = "../modules/permit-key"

  key_id = var.app_environment.signing_key.key_id
}
