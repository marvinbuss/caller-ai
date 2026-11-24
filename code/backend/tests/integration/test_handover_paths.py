"""Requests move along the permitted paths to the agent that fits (feature 007, US3).

The synthetic configuration of the spec's acceptance set: Billing and Technical Support are
routable and have two agents each, Internal is not routable. The first agent of each community is
its entry agent, Billing's agents hand over to each other, Billing connects to Technical Support,
and there is no connection back, so a return to Billing goes through Routing. The model doubles
choose the handover; the tests check the path, the destination and what the destination sees.
"""

import copy
import hashlib
import json

import pytest

from tests.contract.test_text_endpoint import REPLY
from tests.contract.test_text_endpoint import endpoint as endpoint
from tests.doubles import parse_sse
from tests.integration.test_conversation import reviewed_runtime_options as reviewed_runtime_options
from tests.integration.test_voice_delegation_context import delegate, say, voice
from tests.unit.test_admission import admission_case as admission_case
from tests.unit.test_admission import authority_store as authority_store
from tests.unit.test_admission import live_case as live_case
from tests.unit.test_admission import signed_test_permit as signed_test_permit
from tests.unit.test_conversation import text_execution_case as text_execution_case

AGENTS = (
    ("bill-1", "Billing desk", "billing", "Invoices and billing questions"),
    ("bill-2", "Refund desk", "billing", "Refunds and payment plans"),
    ("tech-1", "Support desk", "tech", "Faults and outages"),
    ("tech-2", "Device desk", "tech", "Routers and modems"),
    ("int-1", "Internal desk", "internal", "Internal escalations"),
    ("int-2", "Internal peer", "internal", "Internal reviews"),
)
COMMUNITIES = (
    ("billing", "Billing", "Invoices, payments and refunds", "bill-1", True),
    ("tech", "Technical Support", "Faults, outages and devices", "tech-1", True),
    ("internal", "Internal", "Internal escalations only", "int-1", False),
)
PEERS = (
    ("bill-1", "bill-2"),
    ("bill-2", "bill-1"),
    ("tech-1", "tech-2"),
    ("tech-2", "tech-1"),
    ("int-1", "int-2"),
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def runtime_revision_v2(runtime_revision_v2):
    from caller_runtime.domain.configuration import GRAPH_FIELDS, OPTIONAL_GRAPH_FIELDS, encoded

    def create(number=1):
        document = copy.deepcopy(runtime_revision_v2(number))
        document["schemaVersion"] = "2.3.0"
        document["agents"] = [
            agent for agent in document["agents"] if agent["role"] != "ordinary"
        ] + [
            {
                "agentId": identifier,
                "name": name,
                "description": description,
                "prompt": "Synthetic prompt for " + identifier,
                "enabled": True,
                "role": "ordinary",
                "communityId": community,
                "capabilityAssignments": [],
            }
            for identifier, name, community, description in AGENTS
        ]
        document["communities"] = [
            {
                "communityId": identifier,
                "name": name,
                "routingDescription": description,
                "enabled": True,
                "entryAgentId": entry,
                "routable": routable,
            }
            for identifier, name, description, entry, routable in COMMUNITIES
        ]
        document["relationships"] = [
            {
                "relationshipId": f"peer-{index}",
                "sourceAgentId": source,
                "targetAgentId": target,
                "allowedContextFields": [],
            }
            for index, (source, target) in enumerate(PEERS)
        ]
        document["communityConnections"] = [
            {
                "connectionId": "billing-to-tech",
                "sourceCommunityId": "billing",
                "targetCommunityId": "tech",
                "allowedContextFields": [],
            }
        ]
        document["tools"] = []
        policy = {key: document[key] for key in GRAPH_FIELDS} | {
            key: document[key] for key in OPTIONAL_GRAPH_FIELDS if key in document
        }
        document["policyHash"] = hashlib.sha256(encoded(policy)).hexdigest()
        return json.loads(json.dumps(document))

    return create


def hand(revision, source, target):
    """The `handover_<n>` name under which `source` is offered `target`."""
    from caller_runtime.domain.configuration import RevisionSnapshot
    from caller_runtime.services.handover_rules import destinations

    snapshot = RevisionSnapshot.validate(revision, etag='"paths"')
    (name,) = [item.name for item in destinations(snapshot, source) if item.agent_id == target]
    return name


def handovers(entries):
    return [
        (entry["handover"]["destinationAgentId"], entry["handover"]["path"])
        for entry in entries
        if entry["kind"] == "handover"
    ]


def streamed(requests):
    return [request for request in requests if request.get("stream")]


def quiet(events):
    """Only metadata and the answer reach the user: nothing announces a handover (FR-026)."""
    assert {kind for kind, _ in events} <= {
        "conversation",
        "agent.changed",
        "text.delta",
        "text.completed",
    }
    assert "".join(data["text"] for kind, data in events if kind == "text.delta") == REPLY


# --- Text ---------------------------------------------------------------------------------


def test_text_a2_a8_and_a3_take_the_direct_path_and_return_through_routing(
    endpoint, runtime_revision_v2
):
    revision = runtime_revision_v2()
    endpoint.controls["wire_actions"] = [(hand(revision, "routing", "bill-1"), {})]
    first = parse_sse(endpoint.post(1, text="Why is invoice 9 so high?").text)
    identifier = first[0][1]["conversationId"]
    quiet(first)
    assert endpoint.history.records[identifier]["activeAgentId"] == "bill-1"

    # A2 and A8: a fault is Technical Support's, reached directly through the connection.
    endpoint.controls["wire_actions"] = [(hand(revision, "bill-1", "tech-1"), {})]
    second = parse_sse(endpoint.post(2, conversationId=identifier, text="My line is down.").text)
    quiet(second)
    record = endpoint.history.records[identifier]
    assert (record["activeAgentId"], record["activeCommunityId"]) == ("tech-1", "tech")
    assert handovers(record["entries"])[-1] == ("tech-1", "direct")
    assert endpoint.requests[-1]["instructions"].endswith("Synthetic prompt for tech-1")
    assert "My line is down." in json.dumps(endpoint.requests[-1]["input"])

    # A3: no connection back, so Technical Support names Billing to Routing; Routing asks first.
    endpoint.controls["wire_actions"] = [
        (hand(revision, "tech-1", "routing"), {"community_id": "billing"})
    ]
    third = parse_sse(
        endpoint.post(3, conversationId=identifier, text="Back to the invoice, please.").text
    )
    quiet(third)
    record = endpoint.history.records[identifier]
    assert record["activeAgentId"] == "routing" and record["pendingDestination"] == "billing"

    # The pending destination survives until the next request: only Billing's entry is offered.
    endpoint.controls["wire_actions"] = [(hand(revision, "routing", "bill-1"), {})]
    fourth = parse_sse(endpoint.post(4, conversationId=identifier, text="Invoice 9.").text)
    quiet(fourth)
    routing_request = streamed(endpoint.requests)[-2]
    offered = [
        tool["name"] for tool in routing_request["tools"] if tool["name"].startswith("handover_")
    ]
    assert offered == [hand(revision, "routing", "bill-1")]
    record = endpoint.history.records[identifier]
    assert (record["activeAgentId"], record["pendingDestination"]) == ("bill-1", None)
    assert handovers(record["entries"])[-2:] == [("routing", "routing"), ("bill-1", "routing")]
    # The Billing agent picks up where the earlier exchange stopped.
    sent = json.dumps(endpoint.requests[-1]["input"])
    assert "Why is invoice 9 so high?" in sent and "Back to the invoice, please." in sent


def test_text_a9_reaches_the_second_billing_agent_with_the_corrected_fact(
    endpoint, runtime_revision_v2
):
    revision = runtime_revision_v2()
    endpoint.controls["wire_actions"] = [(hand(revision, "routing", "bill-1"), {})]
    first = parse_sse(endpoint.post(1, text="About invoice 9.").text)
    identifier = first[0][1]["conversationId"]
    endpoint.controls["wire_actions"] = [(hand(revision, "bill-1", "bill-2"), {})]
    second = parse_sse(
        endpoint.post(
            2,
            conversationId=identifier,
            text="Sorry, it is invoice 7, not 9. I want a payment plan for it.",
        ).text
    )
    quiet(second)
    record = endpoint.history.records[identifier]
    assert record["activeAgentId"] == "bill-2"
    assert handovers(record["entries"])[-1] == ("bill-2", "peer")
    assert endpoint.requests[-1]["instructions"].endswith("Synthetic prompt for bill-2")
    assert "invoice 7, not 9" in json.dumps(endpoint.requests[-1]["input"])


def test_text_an_entry_agent_hands_on_to_its_colleague_without_a_reply(
    endpoint, runtime_revision_v2
):
    revision = runtime_revision_v2()
    endpoint.controls["wire_actions"] = [
        (hand(revision, "routing", "bill-1"), {}),
        (hand(revision, "bill-1", "bill-2"), {}),
    ]
    events = parse_sse(endpoint.post(1, text="I need a payment plan.").text)
    # One answer only, from the colleague; the entry agent said nothing.
    quiet(events)
    record = endpoint.history.records[events[0][1]["conversationId"]]
    assert record["activeAgentId"] == "bill-2"
    assert [entry["kind"] for entry in record["entries"]] == [
        "user_text",
        "handover",
        "handover",
        "agent_answer",
    ]
    assert [entry["agentId"] for entry in record["entries"] if entry["kind"] == "agent_answer"] == [
        "bill-2"
    ]


# --- Voice --------------------------------------------------------------------------------


def commentary(socket, delegation_id):
    return [
        event["content"]
        for event in socket.sent
        if event["type"] == "session.commentary.append" and event["delegation_id"] == delegation_id
    ]


@pytest.mark.anyio
async def test_voice_a2_a8_a_topic_change_leaves_the_previously_active_billing_agent(
    live_case, text_execution_case, runtime_revision_v2
):
    revision = runtime_revision_v2()
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controller, segment, socket = await voice(live_case, text_execution_case)
    controls["wire_actions"] = [(hand(revision, "routing", "bill-1"), {})]
    await say(controller, "caller", "Why is invoice 9 so high?", 0, 100)
    await delegate(controller, segment, socket, 100, "Why is invoice 9 so high?")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "bill-1"
    # A8, the reported failure: the next request must not stay with the Billing agent.
    controls["wire_actions"] = [(hand(revision, "bill-1", "tech-1"), {})]
    await say(controller, "caller", "And my line is down.", 2000, 2100)
    await delegate(controller, segment, socket, 2100, "And my line is down.")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "tech-1"
    assert controller.policy.active_community_id == "tech"
    entries = [entry.model_dump(by_alias=True) for entry in controller.history.entries]
    assert handovers(entries) == [("bill-1", "routing"), ("tech-1", "direct")]
    assert requests[-1]["instructions"].endswith("Synthetic prompt for tech-1")
    assert "[Caller, spoken] And my line is down." in json.dumps(requests[-1]["input"])
    assert commentary(socket, "delegation-2100") == [REPLY]
    await segment.close()
    await adapter.close()


@pytest.mark.anyio
async def test_voice_a3_returns_to_billing_through_routing_in_one_delegation(
    live_case, text_execution_case, runtime_revision_v2
):
    revision = runtime_revision_v2()
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controller, segment, socket = await voice(live_case, text_execution_case)
    controls["wire_actions"] = [
        (hand(revision, "routing", "bill-1"), {}),
        (hand(revision, "bill-1", "tech-1"), {}),
    ]
    await say(controller, "caller", "My line is down.", 0, 100)
    await delegate(controller, segment, socket, 100, "My line is down.")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "tech-1"
    controls["wire_actions"] = [
        (hand(revision, "tech-1", "routing"), {"community_id": "billing"}),
        (hand(revision, "routing", "bill-1"), {}),
    ]
    await say(controller, "caller", "Let's get back to the invoice.", 2000, 2100)
    await delegate(controller, segment, socket, 2100, "Let's get back to the invoice.")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "bill-1"
    assert controller.policy.pending_destination is None
    entries = [entry.model_dump(by_alias=True) for entry in controller.history.entries]
    assert handovers(entries)[-2:] == [("routing", "routing"), ("bill-1", "routing")]
    assert "My line is down." in json.dumps(requests[-1]["input"])
    assert commentary(socket, "delegation-2100") == [REPLY]
    await segment.close()
    await adapter.close()


@pytest.mark.anyio
async def test_voice_a9_speech_without_delegation_reaches_the_second_billing_agent(
    live_case, text_execution_case, runtime_revision_v2
):
    revision = runtime_revision_v2()
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controller, segment, socket = await voice(live_case, text_execution_case)
    controls["wire_actions"] = [(hand(revision, "routing", "bill-1"), {})]
    await say(controller, "caller", "About invoice 9.", 0, 100)
    await delegate(controller, segment, socket, 100, "About invoice 9.")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "bill-1"
    # The caller and the voice assistant narrow the request and correct a fact, in speech only.
    await say(controller, "caller", "Sorry, it is invoice 7, not 9.", 2000, 2200)
    await say(controller, "assistant", "Invoice 7, understood.", 2300, 2500)
    await say(controller, "caller", "I want a payment plan for it.", 2600, 2800)
    controls["wire_actions"] = [(hand(revision, "bill-1", "bill-2"), {})]
    await delegate(controller, segment, socket, 2800, "A payment plan for invoice 7.")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "bill-2"
    assert requests[-1]["instructions"].endswith("Synthetic prompt for bill-2")
    sent = requests[-1]["input"]
    assert {"role": "user", "content": "[Caller, spoken] Sorry, it is invoice 7, not 9."} in sent
    assert {
        "role": "assistant",
        "content": "[Voice assistant, spoken] Invoice 7, understood.",
    } in sent
    entries = [entry.model_dump(by_alias=True) for entry in controller.history.entries]
    assert handovers(entries)[-1] == ("bill-2", "peer")
    await segment.close()
    await adapter.close()


@pytest.mark.anyio
async def test_voice_an_entry_agent_hands_on_to_its_colleague_without_a_reply(
    live_case, text_execution_case, runtime_revision_v2
):
    revision = runtime_revision_v2()
    setup, emit, messages, requests, controls, entered, release, adapter = text_execution_case
    controller, segment, socket = await voice(live_case, text_execution_case)
    controls["wire_actions"] = [
        (hand(revision, "routing", "bill-1"), {}),
        (hand(revision, "bill-1", "bill-2"), {}),
    ]
    await say(controller, "caller", "I need a payment plan.", 0, 100)
    await delegate(controller, segment, socket, 100, "I need a payment plan.")
    await controller.process_next(emit=emit)
    assert controller.policy.active_agent_id == "bill-2"
    # Only the colleague's answer goes to the voice assistant; nothing names the agents.
    assert commentary(socket, "delegation-100") == [REPLY]
    spoken = json.dumps([event for event in socket.sent if event["type"] != "session.start"])
    for marker in ("bill-1", "bill-2", "Billing desk", "Refund desk", "handover_"):
        assert marker not in spoken
    await segment.close()
    await adapter.close()
