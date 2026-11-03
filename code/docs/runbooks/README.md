# Operational Runbooks

These guides describe implemented local boundaries and separately pending live qualification.
They are not permission to authenticate, provision, deploy, call, publish, mutate infrastructure
or claim production acceptance. Start with [release gates](../acceptance/release-gates.md) and
[live environment status](../acceptance/live-environment.md).

| Workflow | Guide |
|----------|-------|
| Call admission/media/interruption/closure and safe runtime audit | [Call lifecycle](call-lifecycle.md) |
| Manager graph/roles/conflicts and publication recovery | [Configuration](configuration.md) |
| Ordinary revision refresh, pinning and immediate revocations | [Runtime configuration updates](configuration-updates.md) |
| Same-community visibility and filtered fresh-session transfers | [Communities](communities.md) |
| Reviewed public read-only tools/MCP and current authority | [Capabilities](capabilities.md) |
| Admission, quotas, owner loss, callbacks and rollback | [Scaling](scaling.md) |
| Exact UID-bound pre-drain/retirement and uncertainty | [Drain rollout](drain-rollout.md) |
| Both-service metrics, fixed labels and disabled alert qualification | [Monitoring](monitoring.md) |
| Protected remote state and Terraform verification | [Terraform state](terraform-state.md) |
| Platform composition, reviewed inputs and cost boundaries | [Platform](platform.md) |
| Workload identities, service ownership and private connectivity | [Platform security](platform-security.md) |

No guide substitutes a model instruction, health probe, fake/typed port, PodDisruptionBudget,
stale snapshot, unobserved CI job or generated plan for authorization and measured evidence.
Current revocations override pinned grants. Media/content stays volatile; TTL/backups do not
erase prohibited content. Use bounded safe metadata for diagnosis, never raw conversation or
credential dumps. [Security](../security.md) records dependency and privacy controls.
