# Foundation Contracts

`foundation.openapi.json` is the implementation-owned copy of the accepted health interface
under `specs/001-call-center-agents/contracts/`. Its initial contract version is 1.0.0.

Both applications must satisfy its liveness/readiness schemas. A local ready response does
not certify live call handling or management configuration capability. Neither application
may expose credentials, external endpoint details, or raw dependency errors through probes.

Contract changes require review of the design source, both application consumers, contract
tests and documentation in the same change. Breaking changes require an explicit version
and compatibility decision; do not silently diverge the implementation-owned copy.
