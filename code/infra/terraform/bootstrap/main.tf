locals {
  tags = merge(var.environment.tags, { environment = var.environment.name })
}
