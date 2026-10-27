# Starter Assertion Failure Gate

```text
uv run --project code/tools --locked python code/tools/scripts/verify_failure_gate.py
```

The helper creates one temporary pytest fixture that starts the real runtime application
and asserts the health service identity. Three separate locked backend pytest processes
must prove the correct expectation passes, a deliberately wrong service identity fails with
an actual assertion, and the correct expectation passes again. An empty suite, import error,
startup error or wrong exit code is not evidence of the intended assertion failure.

All inherited caller/Azure/OpenAI settings are removed for the child test. The normal backend
test network restrictions apply. The temporary fixture directory is deleted on success or
failure; no genuine assertion, repository file, branch or user change is altered. A tooling
regression verifies the existing health-test source remains byte-identical.

The native Windows/Linux CI job runs the same helper before the advisory step. Its step fails
if the negative assertion is not observed or restoration/cleanup fails; it does not mark an
unexpected failure as success. Local and now observed remote execution results are recorded
below; neither acceptance of this step nor workflow wiring accepts the entire feature.

Observed locally on 2026-10-23:

```json
{"baseline_exit":0,"broken_exit":1,"restored_exit":0,"fixture_cleaned":true}
```

The helper and its tooling regression both passed. On 2026-10-27, native Ubuntu CI on commit
`c60f7b0738815017777b6433b4fe4d6aa6530b46` also reports the exact JSON above. The observed
[Ubuntu job](https://github.com/marvinbuss/caller-ai/actions/runs/40480335153/job/122108740893)
passed all 384 runtime and 403 management cases before executing this real 0/1/0 assertion
sequence and cleanup. T028 is complete on observed evidence. The later Mermaid-render step
failed, and Windows editor setup also failed; neither full native job is claimed successful.

Published draft PR #1 started Foundation CI on source commit `5c424cd`. The first Windows job
failed before tests because setup-python could not provide pinned Python 3.12.15 on Windows
2022. This is not the intentional assertion failure required by T028. A regression-tested
follow-up runs all helpers through the locked tools environment with the pinned uv action;
project Python pins, permissions and required checks remain unchanged. The later native
Ubuntu run supplies the 0/1/0 evidence above, but full workflow acceptance remains blocked.

The tools findings were remediated on 2026-10-26 with approved-feed virtualenv 21.14.11 and
filelock 4.2.4; current Python/frontend audits pass. Following the user's Docker restart and
build-stage curl/libcurl remediation, the actual complete three-image Trivy gate passes and
T026 is locally complete. See [security gates](../security.md).
Independent local work does not bypass missing image/remote CI evidence or grant release
approval. This helper waives no gate.
