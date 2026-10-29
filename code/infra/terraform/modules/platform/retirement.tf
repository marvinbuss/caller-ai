variable "retirement_guard" {
  description = "Optional reviewed admission definition scope; output only, never automatic cluster installation."
  type = object({
    namespace   = string
    controller  = string
    policy_name = string
  })
  default  = null
  nullable = true

  validation {
    condition = var.retirement_guard == null ? true : alltrue([
      for value in values(var.retirement_guard) : can(regex("^[a-z][a-z0-9-]{0,62}$", value))
    ])
    error_message = "Supply exact reviewed lowercase namespace, dedicated service account and guard names."
  }
}

locals {
  retirement_guard_manifests = var.retirement_guard == null ? null : jsondecode(templatefile("${path.module}/templates/retirement-guard.json.tftpl", {
    namespace          = var.retirement_guard.namespace
    controller_subject = "system:serviceaccount:${var.retirement_guard.namespace}:${var.retirement_guard.controller}"
    policy_name        = var.retirement_guard.policy_name
  }))
}

output "retirement_guard_manifests" {
  description = "Review-only native admission definitions; qualified Terraform-owned installation and RBAC review remain separately authorized."
  value       = local.retirement_guard_manifests
}
