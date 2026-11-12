"""Thin Reflex-owned xyflow bridge; graph authority remains in backend commands.

`ConfigurationGraph` draws the one Community and Agent graph: community nodes hold their agent
nodes, default and custom items differ in fill, border style, icon and label, and the entry,
unreachable and invalid states carry their own icon and label. The browser reports only a pick
or a drawn link back to Reflex; the server decides whether it is allowed.

Positions and collapse state are a per-browser view, never configuration: they live in
`localStorage` under one key per origin and actor reference, hold at most 400 node entries
(oldest evicted first) and never mark the draft as changed. Nodes without a stored position use
the server's deterministic, non-overlapping arrangement. `ConfigurationLeaveGuard` warns before
the page unloads while edits are unpublished. `Interval` calls a Reflex event on a timer while
the page is open.
"""

from typing import Any

import reflex as rx
from reflex_base.utils.imports import ImportDict, ImportVar

STORAGE_LIMIT = 400


def link_args(source: rx.Var[str], target: rx.Var[str]) -> tuple[rx.Var[str], rx.Var[str]]:
    return source, target


def pick_args(identifier: rx.Var[str]) -> tuple[rx.Var[str]]:
    return (identifier,)


LEAVE_CODE = """
function useCallerConfigurationLeaveGuard(dirty) {
    useEffect(() => {
        if (!dirty) return;
        const warn = event => {
            event.preventDefault();
            event.returnValue = "";
        };
        window.addEventListener("beforeunload", warn);
        return () => window.removeEventListener("beforeunload", warn);
    }, [dirty]);
}
function CallerConfigurationLeaveGuard({dirty}) {
    useCallerConfigurationLeaveGuard(dirty);
    return null;
}
"""

VIEW_CODE = """
const callerViewLimit = %(limit)d;
function callerViewKey(actor) {
    return "caller-graph-view:" + window.location.origin + ":" + actor;
}
function callerReadView(actor) {
    try {
        const value = JSON.parse(window.localStorage.getItem(callerViewKey(actor)) || "{}");
        return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch (error) {
        return {};
    }
}
function callerWriteView(actor, id, change) {
    if (!actor) return;
    const view = callerReadView(actor);
    view[id] = {...(view[id] || {}), ...change, t: Date.now()};
    const ids = Object.keys(view);
    if (ids.length > callerViewLimit) {
        ids.sort((a, b) => (view[a].t || 0) - (view[b].t || 0));
        for (const old of ids.slice(0, ids.length - callerViewLimit)) delete view[old];
    }
    try { window.localStorage.setItem(callerViewKey(actor), JSON.stringify(view)); } catch (e) {}
}
"""

GRAPH_CODE = """
const callerNodeId = /^[ac]-[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
function CallerGraphNode({id, data, selected}) {
    const community = data.kind === "community";
    const toggle = event => {
        event.stopPropagation();
        if (data.onToggle) data.onToggle(id);
    };
    const badges = (data.badges || []).map(badge => createElement("span", {
        key: badge.label, className: "caller-node-badge", style: {color: badge.color},
        "data-badge": badge.label
    }, createElement("span", {"aria-hidden": true}, badge.glyph + " "), badge.label));
    return createElement("div", {
        className: "caller-node", "data-kind": data.kind, "data-variant": data.variant,
        "data-selected": selected ? "true" : "false",
        style: {
            background: data.fill, borderColor: data.border, borderStyle: data.borderStyle,
            borderWidth: selected ? 3 : 2, opacity: data.enabled ? 1 : 0.7,
            width: "100%", height: "100%", boxSizing: "border-box", borderRadius: 8,
            padding: "6px 8px", color: "#242925", overflow: "hidden"
        }
    },
        createElement(Handle, {type: "target", position: Position.Top}),
        createElement("div", {style: {display: "flex", gap: 6, alignItems: "center"}},
            createElement("span", {"aria-hidden": true}, data.glyph),
            createElement("strong", {style: {overflowWrap: "anywhere", fontSize: 14}},
                data.label),
            community ? createElement("button", {
                type: "button", onClick: toggle, className: "caller-node-toggle nodrag",
                "aria-label": (data.collapsed ? "Expand " : "Collapse ") + data.label,
                "aria-expanded": !data.collapsed
            }, data.collapsed ? "+" : "\\u2212") : null),
        createElement("div", {style: {display: "flex", flexWrap: "wrap", gap: 6, fontSize: 12}},
            ...badges),
        data.note ? createElement("div", {style: {fontSize: 12}}, data.note) : null,
        community && data.collapsed ? createElement("div", {style: {fontSize: 12}},
            data.count + (data.count === 1 ? " agent" : " agents")) : null,
        createElement(Handle, {type: "source", position: Position.Bottom}));
}
const callerNodeTypes = {caller: CallerGraphNode};
function CallerConfigurationGraph({nodes, edges, actor, dirty, onLink, onPick, guardLeave = true}) {
    const [view, setView] = useState({});
    useEffect(() => { setView(callerReadView(actor)); }, [actor]);
    const toggle = id => {
        const collapsed = !((callerReadView(actor)[id] || {}).collapsed);
        callerWriteView(actor, id, {collapsed});
        setView(callerReadView(actor));
    };
    const shaped = useMemo(() => {
        const collapsed = new Set((nodes || []).filter(node =>
            (view[node.id] || {}).collapsed).map(node => node.id));
        return (nodes || []).map(node => {
            const stored = view[node.id] || {};
            const position = Number.isFinite(stored.x) && Number.isFinite(stored.y)
                ? {x: stored.x, y: stored.y} : node.position;
            const folded = collapsed.has(node.id);
            const style = {...(node.style || {})};
            if (folded) style.height = 96;
            return {
                ...node, position, style, type: "caller",
                hidden: !!node.parentId && collapsed.has(node.parentId),
                data: {...node.data, collapsed: folded, onToggle: toggle}
            };
        });
    }, [nodes, view]);
    const shownEdges = useMemo(() => {
        const hidden = new Set(shaped.filter(node => node.hidden).map(node => node.id));
        return (edges || []).map(edge => ({
            ...edge, hidden: hidden.has(edge.source) || hidden.has(edge.target)
        }));
    }, [edges, shaped]);
    const [localNodes, setLocalNodes, changeNodes] = useNodesState(shaped);
    useEffect(() => { setLocalNodes(shaped); }, [shaped, setLocalNodes]);
    useCallerConfigurationLeaveGuard(dirty && guardLeave);
    const changed = updates => {
        changeNodes(updates);
        for (const update of updates) {
            if (update.type !== "position" || update.dragging === true || !update.position ||
                !callerNodeId.test(update.id)) continue;
            const {x, y} = update.position;
            if (!Number.isFinite(x) || !Number.isFinite(y) ||
                Math.abs(x) > 100000 || Math.abs(y) > 100000) continue;
            callerWriteView(actor, update.id, {x, y});
        }
    };
    return createElement("div", {
        role: "region", "aria-label": "Community and agent graph", "data-testid": "graph",
        style: {height: "520px", width: "100%", minWidth: 0,
            border: "1px solid #8a9690", borderRadius: "6px", background: "#fbfcfc"}
    }, createElement(ReactFlow, {
        nodes: localNodes, edges: shownEdges, onNodesChange: changed, nodeTypes: callerNodeTypes,
        fitView: true, minZoom: 0.2, maxZoom: 2, colorMode: "light",
        deleteKeyCode: null, multiSelectionKeyCode: null,
        onNodeClick: (_event, node) => {
            if (onPick && callerNodeId.test(node.id)) onPick(node.id);
        },
        onConnect: connection => {
            if (onLink && callerNodeId.test(connection.source) &&
                callerNodeId.test(connection.target) &&
                connection.source !== connection.target) {
                onLink(connection.source, connection.target);
            }
        }
    }, createElement(Background, {gap: 24, size: 1, color: "#dce1df"}),
       createElement(Controls, {showInteractive: false})));
}
"""

INTERVAL_CODE = """
function CallerInterval({seconds, onTick}) {
    useEffect(() => {
        if (!onTick || !(seconds > 0)) return;
        const timer = setInterval(() => onTick(), seconds * 1000);
        return () => clearInterval(timer);
    }, [seconds, onTick]);
    return null;
}
"""

NODE_CSS = """
.caller-node-badge { font-weight: 600; }
.caller-node-toggle { margin-left: auto; min-width: 28px; min-height: 28px; border-radius: 4px;
  border: 1px solid #8a9690; background: #ffffff; cursor: pointer; }
.caller-node-toggle:focus-visible { outline: 3px solid #1f5fbf; outline-offset: 2px; }
.react-flow .react-flow__node:focus-visible, .react-flow .react-flow__edge:focus-visible,
.react-flow .react-flow__pane:focus-visible,
.react-flow .react-flow__controls-button:focus-visible {
  outline: 3px solid #1f5fbf; outline-offset: 2px; }
"""


class ConfigurationGraph(rx.Component):
    tag = "CallerConfigurationGraph"
    nodes: rx.Var[list[dict[str, Any]]]
    edges: rx.Var[list[dict[str, Any]]]
    actor: rx.Var[str]
    dirty: rx.Var[bool]
    guard_leave: rx.Var[bool]
    on_link: rx.EventHandler
    on_pick: rx.EventHandler

    def add_imports(self) -> ImportDict:
        return {
            "@xyflow/react@12.12.0": [
                ImportVar(tag=symbol)
                for symbol in (
                    "ReactFlow",
                    "useNodesState",
                    "Background",
                    "Controls",
                    "Handle",
                    "Position",
                )
            ],
            "@xyflow/react/dist/style.css": [ImportVar(tag=None, install=False)],
            "react": [
                ImportVar(tag=symbol)
                for symbol in ("createElement", "useEffect", "useMemo", "useState")
            ],
        }

    def add_custom_code(self) -> list[str]:
        return [LEAVE_CODE, VIEW_CODE % {"limit": STORAGE_LIMIT}, GRAPH_CODE]

    @classmethod
    def get_event_triggers(cls) -> dict[str, Any]:
        return super().get_event_triggers() | {"on_link": link_args, "on_pick": pick_args}


class ConfigurationLeaveGuard(rx.Component):
    tag = "CallerConfigurationLeaveGuard"
    dirty: rx.Var[bool]

    def add_imports(self) -> ImportDict:
        return {"react": [ImportVar(tag="useEffect")]}

    def add_custom_code(self) -> list[str]:
        return [LEAVE_CODE]


class Interval(rx.Component):
    tag = "CallerInterval"
    seconds: rx.Var[int]
    on_tick: rx.EventHandler

    def add_imports(self) -> ImportDict:
        return {"react": [ImportVar(tag="useEffect")]}

    def add_custom_code(self) -> list[str]:
        return [INTERVAL_CODE]

    @classmethod
    def get_event_triggers(cls) -> dict[str, Any]:
        return super().get_event_triggers() | {"on_tick": lambda: []}
