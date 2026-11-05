variable "account_id" {
  description = "Resource ID of the existing AI model account."
  type        = string

  validation {
    condition     = can(regex("^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.CognitiveServices/accounts/[^/]+$", var.account_id))
    error_message = "app_environment.model.account_id must be the resource ID of a Cognitive Services account."
  }
}

variable "text_deployment" {
  description = "Name of the existing model deployment used for text conversations."
  type        = string

  validation {
    condition     = length(var.text_deployment) > 0
    error_message = "app_environment.model.text_deployment must not be empty."
  }
}

variable "live_deployment" {
  description = "Name of the existing realtime model deployment used for speech. Must differ from the text deployment."
  type        = string

  validation {
    condition     = length(var.live_deployment) > 0 && var.live_deployment != var.text_deployment
    error_message = "app_environment.model.live_deployment must be set and differ from text_deployment."
  }
}

variable "runtime_principal_id" {
  description = "Object ID of the runtime's identity, which receives the least role that allows model calls."
  type        = string
}
