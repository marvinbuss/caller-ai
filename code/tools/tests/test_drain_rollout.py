"""Local rollout orchestration cannot substitute grace timers for proven drain/fencing."""

import asyncio
import importlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def case(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    module = importlib.import_module("drain_rollout")
    target = module.Target(
        "cluster-fixture", "runtime", "calls", "owner-fixture", "call-worker", "pod-uid-one"
    )
    request = module.RemovalRequest("operation-fixture", "rollout", target)
    deadlines = []

    class Operations:
        active = True
        authorized = True
        state = "serving"
        failure = None
        override = None
        block = None
        entered = asyncio.Event()

        async def response(self, phase, owner_id, deadline):
            deadlines.append(deadline)
            if not self.authorized:
                raise PermissionError("synthetic-private-auth-details")
            if self.failure == phase:
                raise RuntimeError("synthetic-private-control-details")
            if self.block == phase:
                self.entered.set()
                await asyncio.Event().wait()
            if phase == "begin" and self.state != "drained":
                self.state = "draining"
            if phase == "complete" and not self.active:
                self.state = "drained"
            value = {
                "ownerId": owner_id,
                "state": self.state,
                "canTerminate": self.state == "drained" and not self.active,
            }
            if phase != "status":
                value["outcome"] = (
                    "postponed" if phase == "complete" and self.active else self.state
                )
            if self.override is not None and phase == "status":
                value.update(self.override)
            return value

        async def begin(self, owner_id, *, deadline):
            return await self.response("begin", owner_id, deadline)

        async def complete(self, owner_id, *, deadline):
            return await self.response("complete", owner_id, deadline)

        async def status(self, owner_id, *, deadline):
            return await self.response("status", owner_id, deadline)

    class Cluster:
        protected = True
        failure = None
        mismatch = False
        deleted = []
        held = False
        commits = 0
        seen = set()
        current_uid = target.pod_uid

        async def fence(self, value, *, deadline):
            deadlines.append(deadline)
            if self.failure == "fence":
                raise RuntimeError("synthetic-private-cluster-details")
            self.held = self.protected
            bound = (
                replace(value, target=replace(value.target, pod_uid="different-pod"))
                if self.mismatch
                else value
            )
            return module.RemovalFence(bound, "fence-fixture", self.protected)

        async def retire(self, value, fence, drain, *, deadline):
            deadlines.append(deadline)
            if self.failure == "retire":
                self.commits += 1
                raise RuntimeError("synthetic-private-ambiguous-response")
            if not self.held or not self.protected or self.current_uid != value.target.pod_uid:
                return "postponed"
            assert self.held and fence.request == value
            assert (
                drain["ownerId"] == value.target.owner_id
                and drain["state"] == "drained"
                and drain["canTerminate"] is True
            )
            if value.operation_id in self.seen:
                return "already_retired"
            self.seen.add(value.operation_id)
            self.deleted.append(value.target.pod_uid)
            self.commits += 1
            return "retired"

        def independent_delete(self):
            if self.held:
                raise PermissionError("independent_deletion_fenced")
            pytest.fail("independent_deletion_was_not_fenced")

    return module, request, Operations(), Cluster(), deadlines


def run(case, *, budget=1):
    module, request, operations, cluster, deadlines = case
    return asyncio.run(
        module.orchestrate(request, operations=operations, cluster=cluster, budget_seconds=budget)
    )


def test_guarded_cluster_requires_enforcement_before_ledger_or_deletion(case):
    import kubernetes_rollout

    _module, request, _operations, _cluster, _deadlines = case
    effects = []

    class Guard:
        async def verify(self, request, *, deadline):
            effects.append("guard")
            raise PermissionError("synthetic-enforcement-denial")

    class Ledger:
        async def reserve(self, request):
            effects.append("reserve")
            pytest.fail("denied_enforcement_must_precede_ledger_writes")

    class Pods:
        async def matches(self, request, *, deadline):
            pytest.fail("denied_enforcement_must_precede_cluster_access")

    async def exercise():
        cluster = kubernetes_rollout.GuardedCluster(ledger=Ledger(), guard=Guard(), pods=Pods())
        with pytest.raises(PermissionError):
            await cluster.fence(request, deadline=asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())
    assert effects == ["guard"]


@pytest.mark.parametrize("violation", [None, "actor", "eviction", "uid", "force", "collection"])
def test_native_admission_expression_denies_independent_or_forced_removal(case, violation):
    import celpy
    import kubernetes_rollout

    policy, binding = kubernetes_rollout.admission_manifests(
        "runtime", "retirement-controller", "retirement-guard"
    )
    assert policy["spec"]["failurePolicy"] == "Fail"
    assert binding["spec"]["validationActions"] == ["Deny"]
    incoming = {
        "operation": "DELETE",
        "subResource": "",
        "name": "call-worker",
        "namespace": "runtime",
        "userInfo": {"username": "system:serviceaccount:runtime:retirement-controller"},
        "options": {"preconditions": {"uid": "pod-uid-one"}},
    }
    if violation == "actor":
        incoming["userInfo"]["username"] = "system:serviceaccount:runtime:independent-controller"
    elif violation == "eviction":
        incoming.update(operation="CREATE", subResource="eviction")
    elif violation == "uid":
        incoming["options"]["preconditions"] = {}
    elif violation == "force":
        incoming["options"]["gracePeriodSeconds"] = 0
    elif violation == "collection":
        incoming["name"] = ""
    environment = celpy.Environment()
    compiled = environment.compile(policy["spec"]["validations"][0]["expression"])
    assert bool(
        environment.program(compiled).evaluate({"request": celpy.json_to_cel(incoming)})
    ) is (violation is None)


@pytest.fixture
def kubernetes_case(case):
    import copy

    import httpx
    import kubernetes_rollout

    module, request, operations, _cluster, deadlines = case
    effects, outgoing = [], []
    policy, binding = kubernetes_rollout.admission_manifests(
        "runtime", "retirement-controller", "retirement-guard"
    )
    for index, document in enumerate((policy, binding)):
        document["metadata"].update(uid=f"guard-uid-{index}", generation=1)
    policy["status"] = {"observedGeneration": 1, "typeChecking": {}}
    state = {
        "subject": "system:serviceaccount:runtime:retirement-controller",
        "policy": policy,
        "binding": binding,
        "removed": False,
        "delete_lost": False,
        "accepted_only": False,
        "pod": {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": request.target.pod_name,
                "namespace": "runtime",
                "uid": request.target.pod_uid,
                "labels": {
                    "caller.ai/owner-id": request.target.owner_id,
                    "app.kubernetes.io/name": "calls",
                },
            },
        },
    }

    class Credentials:
        async def bearer_token(self, *, origin, deadline):
            assert origin == "https://cluster.example.invalid"
            deadlines.append(deadline)
            return "synthetic-cluster-credential"

    def respond(value):
        outgoing.append(value)
        assert value.headers["authorization"] == "Bearer synthetic-cluster-credential"
        assert "x-ambient-context" not in value.headers
        assert "cookie" not in value.headers and "?" not in str(value.url)
        if "validatingadmissionpolicies/" in value.url.path:
            return httpx.Response(200, json=state["policy"])
        if "validatingadmissionpolicybindings/" in value.url.path:
            return httpx.Response(200, json=state["binding"])
        if value.url.path.endswith("/selfsubjectreviews"):
            return httpx.Response(
                201, json={"status": {"userInfo": {"username": state["subject"]}}}
            )
        assert value.url.path == "/api/v1/namespaces/runtime/pods/call-worker"
        if value.method == "DELETE":
            document = json.loads(value.content)
            assert document == {
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "preconditions": {"uid": request.target.pod_uid},
                "propagationPolicy": "Foreground",
            }
            effects.append("delete")
            state["removed"] = not state["accepted_only"]
            if state["delete_lost"]:
                raise httpx.ReadError("synthetic-sensitive-lost-response")
            return httpx.Response(
                202, json={"apiVersion": "v1", "kind": "Status", "status": "Success"}
            )
        assert value.method == "GET"
        return httpx.Response(404) if state["removed"] else httpx.Response(200, json=state["pod"])

    class Ledger:
        def __init__(self):
            self.entry = None

        async def reserve(self, value):
            from types import SimpleNamespace

            if self.entry is not None:
                assert self.entry.request == value
            else:
                self.entry = SimpleNamespace(
                    request=copy.deepcopy(value), phase="requested", fence_reference=None
                )
            return copy.deepcopy(self.entry)

        async def inspect(self, value):
            assert self.entry.request == value
            return copy.deepcopy(self.entry)

        async def record_fence(self, value, *, fence_reference):
            assert self.entry.request == value and self.entry.phase == "requested"
            self.entry.phase, self.entry.fence_reference = "fenced", fence_reference
            return copy.deepcopy(self.entry)

        async def begin_retirement(self, value, *, fence_reference):
            assert self.entry.request == value and self.entry.fence_reference == fence_reference
            if self.entry.phase == "submitted":
                return "uncertain"
            if self.entry.phase == "retired":
                return "already_retired"
            assert self.entry.phase == "fenced"
            self.entry.phase = "submitted"
            return "submit"

        async def confirm_retired(self, value, *, fence_reference):
            assert self.entry.request == value and self.entry.phase == "submitted"
            assert self.entry.fence_reference == fence_reference and state["removed"]
            self.entry.phase = "retired"

    ledger = Ledger()

    async def client_scenario(scenario):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            headers={"X-Ambient-Context": "must-not-be-forwarded"},
            cookies={"ambient": "must-not-be-forwarded"},
        ) as http:
            api = kubernetes_rollout.KubernetesRetirementClient(
                cluster_ref=request.target.cluster_ref,
                origin="https://cluster.example.invalid",
                namespace="runtime",
                controller="retirement-controller",
                policy_name="retirement-guard",
                credentials=Credentials(),
                client=http,
            )
            cluster = kubernetes_rollout.GuardedCluster(ledger=ledger, guard=api, pods=api)
            await scenario(cluster, api)

    return module, request, operations, ledger, state, effects, outgoing, client_scenario


@pytest.mark.parametrize(
    "violation", ["subject", "warn_only", "ignore", "stale", "typing", "deleting", "scope"]
)
def test_concrete_kubernetes_guard_denies_unqualified_policy_before_ledger(
    kubernetes_case, violation
):
    _module, request, _operations, ledger, state, effects, _outgoing, scenario = kubernetes_case
    if violation == "subject":
        state["subject"] = "independent-actor"
    elif violation == "warn_only":
        state["binding"]["spec"]["validationActions"] = ["Warn"]
    elif violation == "ignore":
        state["policy"]["spec"]["failurePolicy"] = "Ignore"
    elif violation == "stale":
        state["policy"]["status"]["observedGeneration"] = 0
    elif violation == "typing":
        state["policy"]["status"]["typeChecking"] = {"expressionWarnings": [{"warning": "invalid"}]}
    elif violation == "deleting":
        state["policy"]["metadata"]["deletionTimestamp"] = "2026-10-28T00:00:00Z"
    elif violation == "scope":
        request = replace(request, target=replace(request.target, namespace="foreign"))

    async def exercise(cluster, api):
        with pytest.raises(PermissionError):
            await cluster.fence(request, deadline=asyncio.get_running_loop().time() + 2)

    asyncio.run(scenario(exercise))
    assert ledger.entry is None and not effects


@pytest.mark.parametrize("failure", ["normal", "lost_reply", "accepted_only"])
def test_guarded_retirement_observes_completion_and_never_replays_submission(
    kubernetes_case, failure
):
    import kubernetes_rollout

    module, request, operations, ledger, state, effects, outgoing, scenario = kubernetes_case
    operations.active = False
    state["delete_lost"] = failure == "lost_reply"
    state["accepted_only"] = failure == "accepted_only"

    async def exercise(cluster, api):
        first = await module.orchestrate(request, operations=operations, cluster=cluster)
        assert first.outcome == ("retired" if failure == "normal" else "uncertain")
        assert effects == ["delete"]
        assert ledger.entry.phase == ("retired" if failure == "normal" else "submitted")
        restored = kubernetes_rollout.GuardedCluster(ledger=ledger, guard=api, pods=api)
        if failure == "lost_reply":
            operations.failure = "status"
        second = await module.orchestrate(request, operations=operations, cluster=restored)
        assert (
            second.outcome
            == {"normal": "already_retired", "lost_reply": "retired", "accepted_only": "uncertain"}[
                failure
            ]
        )
        assert effects == ["delete"]

    asyncio.run(scenario(exercise))
    assert sum(value.method == "DELETE" for value in outgoing) == 1


def test_authenticated_operations_http_client_preserves_deadline_and_wire_contract(case):
    import httpx

    module, _request, _operations, _cluster, _deadlines = case
    requests, grants = [], []

    class Credentials:
        async def bearer_token(self, *, origin, deadline):
            grants.append((origin, deadline))
            return "synthetic-operator-credential"

    async def exercise():
        deadline = asyncio.get_running_loop().time() + 2

        async def respond(request):
            requests.append(request)
            assert request.headers["authorization"] == "Bearer synthetic-operator-credential"
            assert await request.aread() == b""
            assert 0 < request.extensions["timeout"]["read"] <= 2
            body = {"ownerId": "owner-fixture", "state": "draining", "canTerminate": False}
            if request.method == "POST":
                body["outcome"] = (
                    "postponed" if request.url.path.endswith("/complete") else "draining"
                )
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = module.HttpOperationsClient(
                base_url="https://runtime.example.invalid", client=http, credentials=Credentials()
            )
            assert (await client.status("owner-fixture", deadline=deadline))["state"] == "draining"
            assert (await client.begin("owner-fixture", deadline=deadline))["outcome"] == "draining"
            assert (await client.complete("owner-fixture", deadline=deadline))[
                "outcome"
            ] == "postponed"
        assert all(
            origin == "https://runtime.example.invalid" and granted == deadline
            for origin, granted in grants
        )

    asyncio.run(exercise())
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/operations/drain/owner-fixture"),
        ("POST", "/v1/operations/drain/owner-fixture"),
        ("POST", "/v1/operations/drain/owner-fixture/complete"),
    ]


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 404, 429, 503])
def test_operations_http_denial_and_redirects_never_retry_or_leak_details(case, status):
    import httpx

    module, *_rest = case
    requests = []

    class Credentials:
        async def bearer_token(self, **keywords):
            return "synthetic-operator-credential"

    async def execute():
        def respond(request):
            requests.append(request)
            return httpx.Response(
                status,
                headers={"Location": "https://different.example.invalid/"},
                text="synthetic-private-error-details",
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), follow_redirects=True
        ) as http:
            client = module.HttpOperationsClient(
                base_url="https://runtime.example.invalid", client=http, credentials=Credentials()
            )
            with pytest.raises(
                PermissionError if status in {401, 403} else RuntimeError
            ) as failure:
                await client.begin("owner-fixture", deadline=asyncio.get_running_loop().time() + 1)
            assert "synthetic-private" not in str(failure.value)
            assert "synthetic-operator-credential" not in str(failure.value)
        assert len(requests) == 1

    asyncio.run(execute())


@pytest.mark.parametrize(
    "violation",
    [
        "duplicate",
        "foreign_owner",
        "unexpected_field",
        "boolean",
        "oversized",
        "not_json",
        "content_type",
    ],
)
def test_operations_http_rejects_unsafe_response_shapes(case, violation):
    import httpx

    module, *_rest = case

    class Credentials:
        async def bearer_token(self, **keywords):
            return "synthetic-operator-credential"

    async def execute():
        def respond(request):
            body = {"ownerId": "owner-fixture", "state": "drained", "canTerminate": True}
            if violation == "duplicate":
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=b'{"ownerId":"owner-fixture","state":"drained","canTerminate":true,"canTerminate":false}',
                )
            if violation == "foreign_owner":
                body["ownerId"] = "foreign-owner"
            elif violation == "unexpected_field":
                body["transcript"] = "synthetic-content"
            elif violation == "boolean":
                body["canTerminate"] = "true"
            elif violation == "oversized":
                return httpx.Response(
                    200, headers={"content-type": "application/json"}, content=b" " * 4097
                )
            elif violation == "not_json":
                return httpx.Response(
                    200, headers={"content-type": "application/json"}, content=b"not-json"
                )
            elif violation == "content_type":
                return httpx.Response(200, text=json.dumps(body))
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = module.HttpOperationsClient(
                base_url="https://runtime.example.invalid", client=http, credentials=Credentials()
            )
            with pytest.raises(RuntimeError, match="operations_response_invalid") as failure:
                await client.status("owner-fixture", deadline=asyncio.get_running_loop().time() + 1)
            assert "synthetic-content" not in str(failure.value)

    asyncio.run(execute())


@pytest.mark.parametrize(
    "origin",
    [
        "http://runtime.example.invalid",
        "https://runtime.example.invalid/path",
        "https://runtime.example.invalid/?override=1",
        "https://runtime.example.invalid/#fragment",
        "https://runtime.example.invalid:8443",
        " https://runtime.example.invalid",
    ],
)
def test_operations_http_rejects_noncanonical_origins_before_network(case, origin):
    import httpx

    module, *_rest = case
    with pytest.raises(ValueError, match="invalid_operations_origin_or_ports"):
        module.HttpOperationsClient(
            base_url=origin, client=httpx.AsyncClient(), credentials=object()
        )


def test_operations_http_does_not_inherit_ambient_auth_cookies_or_query_parameters(case):
    import httpx

    module, *_rest = case

    class Credentials:
        async def bearer_token(self, **keywords):
            return "explicit-fixture-token"

    async def execute():
        def respond(request):
            assert request.headers["authorization"] == "Bearer explicit-fixture-token"
            assert "cookie" not in request.headers and "x-ambient-context" not in request.headers
            assert not request.url.query
            return httpx.Response(
                200, json={"ownerId": "owner-fixture", "state": "serving", "canTerminate": False}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            auth=("ambient-user", "synthetic-ambient-value"),
            cookies={"ambient": "synthetic-cookie"},
            params={"ambient": "synthetic-query"},
            headers={"X-Ambient-Context": "synthetic-header"},
        ) as http:
            client = module.HttpOperationsClient(
                base_url="https://runtime.example.invalid", client=http, credentials=Credentials()
            )
            assert (
                await client.status("owner-fixture", deadline=asyncio.get_running_loop().time() + 1)
            )["state"] == "serving"

    asyncio.run(execute())


@pytest.mark.parametrize(
    "failure_mode", ["expired", "credential_denied", "invalid_token", "canceled"]
)
def test_operations_http_authority_failure_never_sends_a_request(case, failure_mode):
    import httpx

    module, *_rest = case
    requests = []

    class Credentials:
        async def bearer_token(self, **keywords):
            if failure_mode == "credential_denied":
                raise PermissionError("synthetic-private-auth-details")
            if failure_mode == "canceled":
                raise asyncio.CancelledError
            return "invalid token"

    async def execute():
        def respond(request):
            requests.append(request)
            raise AssertionError("unexpected_network_request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = module.HttpOperationsClient(
                base_url="https://runtime.example.invalid", client=http, credentials=Credentials()
            )
            deadline = asyncio.get_running_loop().time() + (-1 if failure_mode == "expired" else 1)
            exception = (
                TimeoutError
                if failure_mode == "expired"
                else asyncio.CancelledError
                if failure_mode == "canceled"
                else PermissionError
            )
            with pytest.raises(exception) as failure:
                await client.status("owner-fixture", deadline=deadline)
            assert "synthetic-private" not in str(failure.value)
        assert not requests

    asyncio.run(execute())


def test_operations_http_cleanup_cannot_extend_original_deadline(case):
    import httpx

    module, *_rest = case

    class Credentials:
        async def bearer_token(self, **keywords):
            return "synthetic-operator-credential"

    class BlockedClose(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"ownerId":"owner-fixture","state":"serving","canTerminate":false}'

        async def aclose(self):
            await asyncio.Event().wait()

    async def execute():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "application/json"}, stream=BlockedClose()
                )
            )
        ) as http:
            client = module.HttpOperationsClient(
                base_url="https://runtime.example.invalid", client=http, credentials=Credentials()
            )
            started = asyncio.get_running_loop().time()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    client.status("owner-fixture", deadline=started + 0.05), timeout=0.5
                )
            assert asyncio.get_running_loop().time() - started < 0.3

    asyncio.run(execute())


def test_active_calls_postpone_without_waiting_or_forced_removal(case):
    module, request, operations, cluster, deadlines = case
    assert run(case).outcome == "postponed"
    assert operations.state == "draining" and not cluster.deleted
    with pytest.raises(PermissionError):
        cluster.independent_delete()
    operations.active = False
    assert run(case).outcome == "retired"
    assert cluster.deleted == [request.target.pod_uid]
    assert len(set(deadlines[:3])) == 1


@pytest.mark.parametrize("intent", ["rollout", "scale_in"])
def test_empty_owner_requires_fresh_completion_then_uid_bound_idempotent_retirement(case, intent):
    module, request, operations, cluster, deadlines = case
    case = (module, replace(request, intent=intent), operations, cluster, deadlines)
    operations.active = False
    assert run(case).outcome == "retired"
    assert run(case).outcome == "already_retired"
    assert cluster.commits == 1 and cluster.deleted == [request.target.pod_uid]
    assert len(set(deadlines[:5])) == 1


@pytest.mark.parametrize("kind", ["missing", "wrong_binding"])
def test_no_drain_or_removal_without_exact_independent_deletion_fence(case, kind):
    module, request, operations, cluster, deadlines = case
    operations.active = False
    cluster.protected, cluster.mismatch = kind != "missing", kind == "wrong_binding"
    assert run(case).outcome == "blocked"
    assert operations.state == "serving" and not cluster.deleted


@pytest.mark.parametrize(
    "override",
    [
        {"ownerId": "other-owner"},
        {"state": "serving"},
        {"canTerminate": False},
        {"canTerminate": "true"},
        {"prompt": "synthetic-private-text"},
    ],
)
def test_stale_or_unexpected_status_never_authorizes_removal(case, override):
    module, request, operations, cluster, deadlines = case
    operations.active, operations.override = False, override
    assert run(case).outcome == "blocked"
    assert not cluster.deleted


def test_operator_denial_has_no_drain_or_delete_and_no_raw_error(case, caplog):
    module, request, operations, cluster, deadlines = case
    operations.authorized = False
    assert run(case).outcome == "blocked"
    assert operations.state == "serving" and not cluster.deleted and not cluster.held
    assert "synthetic-private" not in caplog.text


@pytest.mark.parametrize("change", ["uid_reused", "guard_lost"])
def test_atomic_retirement_port_rejects_changes_after_last_drain_check(case, change):
    module, request, operations, cluster, deadlines = case
    operations.active = False
    retire = cluster.retire

    async def changed(value, fence, drain, *, deadline):
        if change == "uid_reused":
            cluster.current_uid = "replacement-uid"
        else:
            cluster.protected = False
        return await retire(value, fence, drain, deadline=deadline)

    cluster.retire = changed
    assert run(case).outcome == "postponed"
    assert not cluster.deleted and cluster.commits == 0


@pytest.mark.parametrize("phase", ["fence", "begin", "complete", "status", "retire"])
def test_faults_never_claim_confirmed_removal_or_dump_details(case, caplog, phase):
    module, request, operations, cluster, deadlines = case
    operations.active = False
    operations.failure = cluster.failure = phase
    result = run(case)
    assert result.outcome == ("uncertain" if phase == "retire" else "blocked")
    assert "synthetic-private" not in caplog.text + repr(result)
    assert cluster.commits == (1 if phase == "retire" else 0)


def test_original_deadline_cancels_blocked_control_without_retirement(case):
    module, request, operations, cluster, deadlines = case
    operations.block = "complete"
    assert run(case, budget=0.01).outcome == "blocked"
    assert cluster.held and operations.state == "draining" and not cluster.deleted


def test_cancellation_preserves_deletion_fence_and_drain(case):
    module, request, operations, cluster, deadlines = case
    operations.block = "complete"

    async def scenario():
        task = asyncio.create_task(
            module.orchestrate(request, operations=operations, cluster=cluster)
        )
        await operations.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cluster.held and operations.state == "draining" and not cluster.deleted

    asyncio.run(scenario())


@pytest.mark.parametrize("budget", [0, -1, True, float("nan"), float("inf"), 11])
def test_invalid_budget_has_no_control_effects(case, budget):
    module, request, operations, cluster, deadlines = case
    with pytest.raises(ValueError, match="invalid_rollout_budget"):
        run(case, budget=budget)
    assert not deadlines


def test_plan_validation_rejects_unknown_or_duplicate_sensitive_fields(case, capsys, tmp_path):
    module, request, operations, cluster, deadlines = case
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(asdict(request)), encoding="utf-8")
    assert module.main(["--check-plan", str(plan)]) == 0
    value = asdict(request) | {"credential": "synthetic-private-token"}
    plan.write_text(json.dumps(value), encoding="utf-8")
    assert module.main(["--check-plan", str(plan)]) == 2
    plan.write_text('{"intent":"rollout","intent":"scale_in"}', encoding="utf-8")
    assert module.main(["--check-plan", str(plan)]) == 2
    assert module.main(["--execute", "synthetic-private-token"]) == 2
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err
    assert not cluster.deleted


def test_cli_without_reviewed_ports_cannot_execute_cluster_actions(case, capsys):
    module, request, operations, cluster, deadlines = case
    assert module.main([]) == 2
    assert not deadlines
