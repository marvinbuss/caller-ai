# Caller AI

Two independently owned Python services: a FastAPI conversation runtime and a Reflex management
application. Default startup is credential-free; configured browser conversations require
explicit reviewed resources. Local synthetic tests are not live-provider or device qualification.

- [Setup and verification](docs/setup.md)
- [Architecture](docs/architecture.md)
- [Community, agent and published-test configuration](docs/configuration-guide.md)
- [Repository ownership](docs/repository-layout.md)
- [Operational runbooks](docs/runbooks/README.md)
- [Versioned application contracts](code/contracts/README.md)
- [Current feature implementation and open qualification gates](specs/002-multimodal-agent-management/tasks.md)

Maintained documentation lives only under `docs/`. Feature specifications remain under `specs/`;
historical acceptance records retain their original scope, results and deferred gates.
