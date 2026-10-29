"""Explicit guarded retirement over durable authority; no ambient cluster credentials."""

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from string import Template
from typing import Any, Literal, Protocol

import httpx
from drain_rollout import (
    OperationsCredentials,
    RemovalFence,
    RemovalRequest,
    reference,
    snapshot,
    unique_object,
)

ADMISSION_GROUP = "/apis/admissionregistration.k8s.io/v1/"


def admission_manifests(
    namespace: str, controller: str, name: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    for value in (namespace, controller, name):
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value):
            raise ValueError("invalid_retirement_guard_scope")
    path = (
        Path(__file__).parents[2]
        / "infra/terraform/modules/platform/templates/retirement-guard.json.tftpl"
    )
    documents = json.loads(
        Template(path.read_text(encoding="utf-8")).substitute(
            namespace=namespace,
            controller_subject=f"system:serviceaccount:{namespace}:{controller}",
            policy_name=name,
        )
    )
    return documents[0], documents[1]


class RemovalRecord(Protocol):
    @property
    def phase(self) -> str: ...

    @property
    def fence_reference(self) -> str | None: ...


class RemovalLedger(Protocol):
    async def reserve(self, request: Mapping[str, Any]) -> RemovalRecord: ...

    async def inspect(self, request: Mapping[str, Any]) -> RemovalRecord: ...

    async def record_fence(
        self, request: Mapping[str, Any], *, fence_reference: str
    ) -> RemovalRecord: ...

    async def begin_retirement(
        self, request: Mapping[str, Any], *, fence_reference: str
    ) -> Literal["submit", "uncertain", "already_retired"]: ...

    async def confirm_retired(
        self, request: Mapping[str, Any], *, fence_reference: str
    ) -> None: ...


class EnforcementPort(Protocol):
    async def verify(self, request: RemovalRequest, *, deadline: float) -> str:
        """Verify installed independent admission enforcement and return its stable binding."""
        ...


class PodRemovalPort(Protocol):
    async def matches(self, request: RemovalRequest, *, deadline: float) -> bool: ...

    async def delete(self, request: RemovalRequest, *, deadline: float) -> None:
        """Submit exactly one non-forced UID-conditional deletion under controller identity."""
        ...

    async def removed(self, request: RemovalRequest, *, deadline: float) -> bool:
        """Independently observe absence of the exact old pod UID; acceptance is not removal."""
        ...


def require_deadline(deadline: float) -> None:
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("invalid_cluster_deadline")
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining > 10:
        raise ValueError("invalid_cluster_deadline")
    if remaining <= 0:
        raise TimeoutError("cluster_deadline_exceeded")


class GuardedCluster:
    def __init__(
        self, *, ledger: RemovalLedger, guard: EnforcementPort, pods: PodRemovalPort
    ) -> None:
        if any(port is None for port in (ledger, guard, pods)):
            raise ValueError("explicit_cluster_ports_required")
        self._ledger, self._guard, self._pods = ledger, guard, pods

    async def _binding(self, request: RemovalRequest, deadline: float) -> str:
        binding = await self._guard.verify(request, deadline=deadline)
        reference(binding)
        return binding

    async def reconcile(self, request: RemovalRequest, *, deadline: float) -> str:
        require_deadline(deadline)
        async with asyncio.timeout_at(deadline):
            binding = await self._binding(request, deadline)
            operation = asdict(request)
            entry = await self._ledger.reserve(operation)
            if entry.phase == "requested":
                return "pending"
            if entry.fence_reference != binding:
                raise PermissionError("cluster_fence_binding_invalid")
            if entry.phase == "fenced":
                return "pending"
            if entry.phase == "retired":
                return "already_retired"
            if entry.phase != "submitted":
                raise RuntimeError("invalid_removal_recovery_state")
            if not await self._pods.removed(request, deadline=deadline):
                return "uncertain"
            await self._ledger.confirm_retired(operation, fence_reference=binding)
            return "retired"

    async def fence(self, request: RemovalRequest, *, deadline: float) -> RemovalFence:
        require_deadline(deadline)
        async with asyncio.timeout_at(deadline):
            binding = await self._binding(request, deadline)
            operation = asdict(request)
            entry = await self._ledger.reserve(operation)
            if entry.phase == "requested":
                if not await self._pods.matches(request, deadline=deadline):
                    raise PermissionError("cluster_target_binding_invalid")
                await self._ledger.record_fence(operation, fence_reference=binding)
            elif (
                entry.phase not in {"fenced", "submitted", "retired"}
                or entry.fence_reference != binding
            ):
                raise PermissionError("cluster_fence_binding_invalid")
            return RemovalFence(request, binding, True)

    async def retire(
        self,
        request: RemovalRequest,
        fence: RemovalFence,
        drain: Mapping[str, Any],
        *,
        deadline: float,
    ) -> str:
        require_deadline(deadline)
        async with asyncio.timeout_at(deadline):
            current = snapshot(drain, request.target.owner_id, "status")
            if (
                fence.request != request
                or fence.independent_deletions_blocked is not True
                or current["state"] != "drained"
                or current["canTerminate"] is not True
                or await self._binding(request, deadline) != fence.reference
            ):
                raise PermissionError("guarded_removal_not_authorized")
            operation = asdict(request)
            entry = await self._ledger.inspect(operation)
            if entry.fence_reference != fence.reference or entry.phase not in {
                "fenced",
                "submitted",
                "retired",
            }:
                raise PermissionError("cluster_fence_binding_invalid")
            if entry.phase == "retired":
                return "already_retired"
            if entry.phase == "fenced" and not await self._pods.matches(request, deadline=deadline):
                return "postponed"
            decision = await self._ledger.begin_retirement(
                operation, fence_reference=fence.reference
            )
            if decision == "already_retired":
                return "already_retired"
            if decision == "submit":
                if await self._binding(request, deadline) != fence.reference:
                    raise PermissionError("cluster_fence_binding_invalid")
                await self._pods.delete(request, deadline=deadline)
            elif decision != "uncertain":
                raise RuntimeError("invalid_removal_submission_state")
            if not await self._pods.removed(request, deadline=deadline):
                return "uncertain"
            await self._ledger.confirm_retired(operation, fence_reference=fence.reference)
            return "retired"


class KubernetesRetirementClient:
    """Explicit authenticated Kubernetes REST ports; observed policy is never installed here."""

    def __init__(
        self,
        *,
        cluster_ref: str,
        origin: str,
        namespace: str,
        controller: str,
        policy_name: str,
        credentials: OperationsCredentials,
        client: httpx.AsyncClient,
    ) -> None:
        reference(cluster_ref)
        endpoint = httpx.URL(origin)
        if (
            endpoint.scheme != "https"
            or endpoint.username
            or endpoint.password
            or not endpoint.host
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
            or client is None
            or credentials is None
        ):
            raise ValueError("explicit_kubernetes_origin_and_ports_required")
        self._policy, self._binding = admission_manifests(namespace, controller, policy_name)
        self._cluster, self._namespace = cluster_ref, namespace
        self._subject = f"system:serviceaccount:{namespace}:{controller}"
        self._origin, self._client, self._credentials = (
            str(endpoint).rstrip("/"),
            client,
            credentials,
        )

    def _scope(self, request: RemovalRequest) -> None:
        if (
            request.target.cluster_ref != self._cluster
            or request.target.namespace != self._namespace
        ):
            raise PermissionError("cluster_scope_not_authorized")

    async def _request(
        self,
        method: str,
        path: str,
        deadline: float,
        *,
        body: Mapping[str, Any] | None = None,
        allow_absent: bool = False,
    ) -> dict[str, Any] | None:
        require_deadline(deadline)
        try:
            async with asyncio.timeout_at(deadline):
                token = await self._credentials.bearer_token(origin=self._origin, deadline=deadline)
                if (
                    not isinstance(token, str)
                    or not 1 <= len(token) <= 8192
                    or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token)
                ):
                    raise PermissionError("cluster_credentials_unavailable")
                outgoing = httpx.Request(
                    method,
                    self._origin + path,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(body, separators=(",", ":")).encode()
                    if body is not None
                    else b"",
                    extensions={
                        "timeout": httpx.Timeout(
                            max(0.001, deadline - asyncio.get_running_loop().time())
                        ).as_dict()
                    },
                )
                response = await self._client.send(
                    outgoing, stream=True, auth=None, follow_redirects=False
                )
                try:
                    if response.status_code in {401, 403}:
                        raise PermissionError("cluster_permission_denied")
                    if response.status_code == 404 and allow_absent:
                        return None
                    if (
                        response.status_code not in {200, 201, 202}
                        or response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        != "application/json"
                    ):
                        raise RuntimeError("cluster_control_unavailable")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 65536:
                            raise ValueError("cluster_response_too_large")
                    document = json.loads(content, object_pairs_hook=unique_object)
                    if not isinstance(document, dict):
                        raise ValueError("invalid_cluster_response")
                    return document
                finally:
                    await response.aclose()
        except PermissionError:
            raise PermissionError("cluster_permission_denied") from None
        except TimeoutError:
            raise TimeoutError("cluster_deadline_exceeded") from None
        except Exception:
            raise RuntimeError("cluster_control_unavailable") from None

    async def verify(self, request: RemovalRequest, *, deadline: float) -> str:
        self._scope(request)
        policy_name = self._policy["metadata"]["name"]
        policy = await self._request(
            "GET", ADMISSION_GROUP + "validatingadmissionpolicies/" + policy_name, deadline
        )
        binding = await self._request(
            "GET", ADMISSION_GROUP + "validatingadmissionpolicybindings/" + policy_name, deadline
        )
        identity = await self._request(
            "POST",
            "/apis/authentication.k8s.io/v1/selfsubjectreviews",
            deadline,
            body={"apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview"},
        )
        if policy is None or binding is None or identity is None:
            raise PermissionError("independent_removal_guard_unavailable")
        versions = []
        for actual, expected in ((policy, self._policy), (binding, self._binding)):
            metadata = actual.get("metadata", {})
            if (
                actual.get("apiVersion") != expected["apiVersion"]
                or actual.get("kind") != expected["kind"]
                or metadata.get("name") != expected["metadata"]["name"]
                or actual.get("spec") != expected["spec"]
                or metadata.get("deletionTimestamp") is not None
                or type(metadata.get("generation")) is not int
                or metadata["generation"] < 1
                or not isinstance(metadata.get("uid"), str)
            ):
                raise PermissionError("independent_removal_guard_invalid")
            reference(metadata["uid"])
            versions.append((metadata["uid"], metadata["generation"]))
        if (
            policy.get("status", {}).get("observedGeneration") != policy["metadata"]["generation"]
            or policy["status"].get("typeChecking") not in ({}, {"expressionWarnings": []})
            or identity.get("status", {}).get("userInfo", {}).get("username") != self._subject
        ):
            raise PermissionError("independent_removal_guard_not_ready")
        return (
            "fence-"
            + hashlib.sha256(json.dumps(versions, separators=(",", ":")).encode()).hexdigest()
        )

    async def _pod(self, request: RemovalRequest, deadline: float) -> dict[str, Any] | None:
        self._scope(request)
        pod = await self._request("GET", self._path(request), deadline, allow_absent=True)
        if pod is None:
            return None
        metadata = pod.get("metadata", {})
        if (
            pod.get("apiVersion") != "v1"
            or pod.get("kind") != "Pod"
            or metadata.get("name") != request.target.pod_name
            or metadata.get("namespace") != request.target.namespace
            or not isinstance(metadata.get("uid"), str)
        ):
            raise RuntimeError("invalid_pod_observation")
        reference(metadata["uid"])
        return pod

    @staticmethod
    def _path(request: RemovalRequest) -> str:
        return f"/api/v1/namespaces/{request.target.namespace}/pods/{request.target.pod_name}"

    async def matches(self, request: RemovalRequest, *, deadline: float) -> bool:
        pod = await self._pod(request, deadline)
        if pod is None:
            return False
        metadata = pod["metadata"]
        labels = metadata.get("labels", {})
        return bool(
            metadata["uid"] == request.target.pod_uid
            and metadata.get("deletionTimestamp") is None
            and labels.get("caller.ai/owner-id") == request.target.owner_id
            and labels.get("app.kubernetes.io/name") == request.target.workload
        )

    async def delete(self, request: RemovalRequest, *, deadline: float) -> None:
        self._scope(request)
        await self._request(
            "DELETE",
            self._path(request),
            deadline,
            body={
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "preconditions": {"uid": request.target.pod_uid},
                "propagationPolicy": "Foreground",
            },
            allow_absent=True,
        )

    async def removed(self, request: RemovalRequest, *, deadline: float) -> bool:
        pod = await self._pod(request, deadline)
        return pod is None or pod["metadata"]["uid"] != request.target.pod_uid
