resource "terraform_data" "rotation" {
  # A new key ID replaces the key pair, which is how a rotation starts (FR-051).
  input = var.key_id
}

resource "tls_private_key" "permit" {
  algorithm = "RSA"
  rsa_bits  = 2048

  lifecycle {
    replace_triggered_by = [terraform_data.rotation]
  }
}
