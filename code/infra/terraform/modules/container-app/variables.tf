variable "name" {
  description = "Container app name, 2 to 32 lowercase letters, digits or hyphens."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$", var.name))
    error_message = "The app name must be 2 to 32 lowercase letters, digits or hyphens."
  }
}

variable "resource_group_name" {
  description = "Resource group of the container app."
  type        = string
}

variable "container_app_environment_id" {
  description = "Resource ID of the Container Apps environment."
  type        = string
}

variable "user_assigned_identity_id" {
  description = "Resource ID of the user-assigned identity that the app uses for Key Vault references and Azure calls."
  type        = string
}

variable "image_repository" {
  description = "Image repository, for example ghcr.io/marvinbuss/caller-ai/runtime."
  type        = string
}

variable "image_tag" {
  description = "Image tag, sha-<40 hex> or v<semver>. Exactly one of image_tag and image_digest is set."
  type        = string
  default     = null

  validation {
    condition     = var.image_tag == null || can(regex("^(sha-[0-9a-f]{40}|v[0-9]+\\.[0-9]+\\.[0-9]+)$", var.image_tag))
    error_message = "The image tag must match sha-<40 hex> or v<semver>. latest is not allowed."
  }
}

variable "image_digest" {
  description = "Image digest, sha256:<64 hex>. Exactly one of image_tag and image_digest is set."
  type        = string
  default     = null

  validation {
    condition     = var.image_digest == null || can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "The image digest must match sha256:<64 hex>."
  }
}

variable "cpu" {
  description = "CPU cores of the container (Consumption profile)."
  type        = number
  default     = 0.5

  validation {
    condition     = contains([0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 4], var.cpu)
    error_message = "The CPU must be a valid Consumption value."
  }
}

variable "memory" {
  description = "Memory of the container, a valid Consumption combination with cpu (default 1Gi for 0.5 cores)."
  type        = string
  default     = "1Gi"

  validation {
    condition     = contains(["0.5Gi", "1Gi", "1.5Gi", "2Gi", "2.5Gi", "3Gi", "3.5Gi", "4Gi", "8Gi"], var.memory)
    error_message = "The memory must be a valid Consumption value."
  }
}

variable "min_replicas" {
  description = "Minimum replicas. Fixed to 1: sessions are kept in memory."
  type        = number
  default     = 1

  validation {
    condition     = var.min_replicas == 1
    error_message = "Exactly one replica is supported (in-memory sessions)."
  }
}

variable "max_replicas" {
  description = "Maximum replicas. Fixed to 1: sessions are kept in memory."
  type        = number
  default     = 1

  validation {
    condition     = var.max_replicas == 1
    error_message = "Exactly one replica is supported (in-memory sessions)."
  }
}

variable "target_port" {
  description = "Container port that ingress forwards to."
  type        = number
}

variable "settings" {
  description = "Plain environment settings. Secrets must use secret_references instead."
  type        = map(string)
  default     = {}
}

variable "secret_references" {
  description = "Map from environment variable name to the versionless Key Vault secret ID that the app resolves with its identity."
  type        = map(string)
  default     = {}
}

variable "public_access_enabled" {
  description = "Whether the app ingress is reachable from outside the environment."
  type        = bool
  default     = false
}

variable "allowed_cidrs" {
  description = "Source ranges allowed to reach the ingress. Required when public access is enabled."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.allowed_cidrs : can(cidrhost(c, 0))])
    error_message = "Every allowed range must be a valid CIDR."
  }
}

variable "tags" {
  description = "Tags applied to the container app."
  type        = map(string)
  default     = {}
}
