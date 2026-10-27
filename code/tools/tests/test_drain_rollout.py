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
