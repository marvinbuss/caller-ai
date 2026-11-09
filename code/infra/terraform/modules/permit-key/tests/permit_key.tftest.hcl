mock_provider "tls" {}

variables {
  key_id = "int-1"
}

run "generates_an_rsa_2048_pair" {
  command = plan

  assert {
    condition     = tls_private_key.permit.algorithm == "RSA" && tls_private_key.permit.rsa_bits == 2048
    error_message = "The permit key must be RSA 2048."
  }
}

run "exposes_the_key_id" {
  command = plan

  assert {
    condition     = output.key_id == "int-1"
    error_message = "The key ID must be passed through."
  }
}

run "rejects_invalid_key_id" {
  command = plan

  variables {
    key_id = "-bad"
  }

  expect_failures = [var.key_id]
}
