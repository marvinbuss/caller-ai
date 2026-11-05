# monitoring

Creates the Log Analytics workspace, a workspace-based Application Insights component, an action group
and the alerts for the container apps.

## Purpose

Container logs and metrics go to the workspace. Alerts cover platform signals (5xx requests, restarts,
replicas below one, CPU, memory) and log-query alerts over the applications' structured events for
dependency failures and latency (Constitution Principle VII).

## Inputs

See [variables.tf](variables.tf). Every alert needs an owner and an https runbook link, or validation
fails. Retention is 30 to 730 days.

## Outputs

See [outputs.tf](outputs.tf): workspace, Application Insights, action group and alert names.

## Behavior and failure

The metric names and the event fields in the log queries are confirmed on the first deployment
(research R-18) and the thresholds are tuned there. Alert delivery is tested with an action group test
notification by the environment check.

## Security assumptions

Logs exclude sensitive call content by default (FR-033). A private link scope for the workspace is added
by the root.

## References

- [Container Apps monitoring](https://learn.microsoft.com/azure/container-apps/observability)
- [Azure Monitor alerts](https://learn.microsoft.com/azure/azure-monitor/alerts/alerts-overview)
