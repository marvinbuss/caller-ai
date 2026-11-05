# Operational Runbooks

These guides describe implemented local boundaries and separately pending live qualification.
They are not permission to authenticate, provision, deploy, call, publish, mutate infrastructure
or claim production acceptance. Start with [release gates](../acceptance/release-gates.md) and
[live environment status](../acceptance/live-environment.md).

| Workflow | Guide |
|----------|-------|
| Text and voice endpoints, errors, limits and slots | [Conversation endpoints](../conversation-endpoints.md) |
| Stored text conversations, retention, purge and recovery | [Text history](text-history.md) |
| Manager graph/roles/conflicts and publication recovery | [Configuration](configuration.md) |
| Ordinary revision refresh, pinning and immediate revocations | [Runtime configuration updates](configuration-updates.md) |
| Same-community visibility and filtered fresh-session transfers | [Communities](communities.md) |
| Reviewed public read-only tools/MCP and current authority | [Capabilities](capabilities.md) |
| Limits, admission, voice-session behavior, instance loss and rollback | [Scaling](scaling.md) |
| Both-service metrics, fixed labels and environment alerts | [Monitoring](monitoring.md) |
| Protected remote state, first creation and recovery | [Terraform state](terraform-state.md) |
| The three roots, plan, protected apply, repeat check and teardown | [Terraform delivery](terraform-delivery.md) |
| Services, modules and reviewed inputs of an environment | [Platform](platform.md) |
| Identities, roles, network boundaries and secrets | [Platform security](platform-security.md) |
| Bringing an environment up, changing, recovering and removing it | [Environment bring-up](environment-bring-up.md) |
| Public access flag, allow-list, enabling, disabling and verifying | [Public access](public-access.md) |
| Image tags, scans, public packages and rollback | [Image publishing](image-publishing.md) |
| Application live settings, reviewed composition and reason codes | [Application live mode](application-live-mode.md) |

No guide substitutes a model instruction, health probe, fake/typed port, replica setting,
stale snapshot, unobserved CI job or generated plan for authorization and measured evidence.
Current revocations override pinned grants. Media/content stays volatile; TTL/backups do not
erase prohibited content. Use bounded safe metadata for diagnosis, never raw conversation or
credential dumps. [Security](../security.md) records dependency and privacy controls.
