"""Thin Reflex-owned xyflow bridge; graph authority remains in backend commands."""

from typing import Any

import reflex as rx
from reflex_base.utils.imports import ImportDict, ImportVar


def move_args(
    identifier: rx.Var[str], x: rx.Var[float], y: rx.Var[float]
) -> tuple[rx.Var[str], rx.Var[float], rx.Var[float]]:
    return identifier, x, y


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


GRAPH_CODE = """
function CallerConfigurationGraph({
    nodes, edges, viewKey, dirty, onMove, onLink, onPick, guardLeave = true
}) {
    const [localNodes, setLocalNodes, changeNodes] = useNodesState(nodes || []);
    const pending = useRef(new Map());
    const currentView = useRef(viewKey);
    useEffect(() => {
        if (currentView.current !== viewKey || !dirty) {
            pending.current.clear();
            currentView.current = viewKey;
        }
        setLocalNodes((nodes || []).map(node => {
            const position = pending.current.get(node.id);
            if (!position) return node;
            if (node.position.x === position.x && node.position.y === position.y) {
                pending.current.delete(node.id);
                return node;
            }
            return {...node, position};
        }));
    }, [nodes, viewKey, dirty, setLocalNodes]);
    useCallerConfigurationLeaveGuard(dirty && guardLeave);
    const validId = value => typeof value === "string" &&
        /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value);
    const changed = updates => {
        changeNodes(updates);
        for (const update of updates) {
            if (update.type !== "position" || update.dragging === true || !update.position ||
                !validId(update.id)) continue;
            const {x, y} = update.position;
            if (!Number.isFinite(x) || !Number.isFinite(y) ||
                Math.abs(x) > 100000 || Math.abs(y) > 100000) continue;
            const original = (nodes || []).find(node => node.id === update.id);
            if (!original) continue;
            const previous = pending.current.get(update.id) || original.position;
            if (previous.x === x && previous.y === y) continue;
            if (!pending.current.has(update.id) && pending.current.size >= 200) continue;
            pending.current.set(update.id, {x, y});
            if (onMove) onMove(update.id, x, y);
        }
    };
    return createElement("div", {
        role: "region",
        "aria-label": viewKey === "communities" ? "Community graph" : "Agent graph",
        style: {height: "420px", width: "100%", minWidth: 0,
            border: "1px solid #dce1df", borderRadius: "4px", background: "#fbfcfc"}
    }, createElement(ReactFlow, {
        nodes: localNodes, edges: edges || [], onNodesChange: changed,
        fitView: true, minZoom: 0.2, maxZoom: 2, colorMode: "light",
        deleteKeyCode: null, multiSelectionKeyCode: null,
        onNodeClick: (_event, node) => {
            if (onPick && validId(node.id)) onPick(node.id);
        },
        onConnect: connection => {
            if (onLink && validId(connection.source) && validId(connection.target) &&
                connection.source !== connection.target) {
                onLink(connection.source, connection.target);
            }
        }
    }, createElement(Background, {gap: 24, size: 1, color: "#dce1df"}),
       createElement(Controls, {showInteractive: false})));
}
"""


class ConfigurationGraph(rx.Component):
    tag = "CallerConfigurationGraph"
    nodes: rx.Var[list[dict[str, Any]]]
    edges: rx.Var[list[dict[str, Any]]]
    view_key: rx.Var[str]
    dirty: rx.Var[bool]
    guard_leave: rx.Var[bool]
    on_move: rx.EventHandler
    on_link: rx.EventHandler
    on_pick: rx.EventHandler

    def add_imports(self) -> ImportDict:
        return {
            "@xyflow/react@12.12.0": [
                ImportVar(tag=symbol)
                for symbol in ("ReactFlow", "useNodesState", "Background", "Controls")
            ],
            "@xyflow/react/dist/style.css": [ImportVar(tag=None, install=False)],
            "react": [ImportVar(tag=symbol) for symbol in ("createElement", "useEffect", "useRef")],
        }

    def add_custom_code(self) -> list[str]:
        return [LEAVE_CODE, GRAPH_CODE]

    @classmethod
    def get_event_triggers(cls) -> dict[str, Any]:
        return super().get_event_triggers() | {
            "on_move": move_args,
            "on_link": link_args,
            "on_pick": pick_args,
        }


class ConfigurationLeaveGuard(rx.Component):
    tag = "CallerConfigurationLeaveGuard"
    dirty: rx.Var[bool]

    def add_imports(self) -> ImportDict:
        return {"react": [ImportVar(tag="useEffect")]}

    def add_custom_code(self) -> list[str]:
        return [LEAVE_CODE]
