"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Effect, TransactionDetail } from "@/lib/types";
import { isInFlight, stateClasses, TERMINAL_TX_STATES } from "@/lib/states";
import { money } from "@/lib/format";

type EffectNodeData = { effect: Effect; selected: boolean };
type EffectNode = Node<EffectNodeData, "effect">;

const COL_W = 276;
const ROW_H = 118;

function EffectNodeView({ data }: NodeProps<EffectNode>) {
  const e = data.effect;
  const c = stateClasses(e.state);
  const dispatch = e.dispatch_result?.outcome;
  const verify = e.verification_result?.status;
  return (
    <div
      className={`w-[240px] rounded-xl border-l-4 px-3 py-2 text-left ${c.border} ${
        data.selected ? "neu-inset outline-2 outline-offset-2 outline-indigo-600" : "neu-raised-sm"
      } ${isInFlight(e.state) ? "animate-pulse" : ""}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-slate-500" />
      <div className="font-mono text-[13px] font-semibold text-ink">{e.effect_type}</div>
      <div className="font-mono text-xs text-mute">
        {e.actor_id}
        {e.amount ? ` · ${money(e.amount)}` : ""}
      </div>
      <div className={`mt-0.5 font-mono text-xs font-semibold ${c.text}`}>{e.state}</div>
      {(dispatch || verify) && (
        <div className="mt-0.5 flex flex-wrap gap-x-2 font-mono text-xs">
          {dispatch && <span className={stateClasses(dispatch).text}>said:{dispatch}</span>}
          {verify && <span className={stateClasses(verify).text}>real:{verify.replace("VERIFIED_", "")}</span>}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-slate-500" />
    </div>
  );
}

const nodeTypes = { effect: EffectNodeView };

export function EffectDag({
  detail,
  selectedId,
  onSelect,
}: {
  detail: TransactionDetail;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const { nodes, edges, height } = useMemo(() => {
    const byLevel = new Map<number, Effect[]>();
    for (const e of detail.effects) byLevel.set(e.level, [...(byLevel.get(e.level) ?? []), e]);
    const maxRows = Math.max(1, ...[...byLevel.values()].map((l) => l.length));
    const nodes: EffectNode[] = [];
    for (const [level, list] of byLevel) {
      const offset = ((maxRows - list.length) * ROW_H) / 2;
      list.forEach((e, i) =>
        nodes.push({
          id: e.id,
          type: "effect",
          position: { x: level * COL_W, y: offset + i * ROW_H },
          data: { effect: e, selected: e.id === selectedId },
          draggable: false,
        }),
      );
    }
    const byId = new Map(detail.effects.map((e) => [e.id, e]));
    const txLive = !TERMINAL_TX_STATES.has(detail.transaction.state);
    const edges: Edge[] = detail.graph.edges.map((ed) => {
      const src = byId.get(ed.from);
      const dst = byId.get(ed.to);
      const waiting =
        txLive && !!dst && ["PREPARED", "VALIDATED"].includes(dst.state) && !!src && src.state !== "VERIFIED" && src.state !== "ABORTED";
      const color = src ? stateClasses(src.state).hex : "#7b879d";
      return {
        id: `${ed.from}->${ed.to}`,
        source: ed.from,
        target: ed.to,
        animated: waiting,
        style: { stroke: color, strokeWidth: waiting ? 2 : 1.5, opacity: 0.8 },
        markerEnd: { type: MarkerType.ArrowClosed, color },
      };
    });
    return { nodes, edges, height: Math.max(220, maxRows * ROW_H + 40) };
  }, [detail, selectedId]);

  if (!detail.effects.length) {
    return <div className="p-6 text-sm text-faint">No effects proposed in this transaction tree.</div>;
  }

  return (
    <div style={{ height }} className="neu-inset w-full overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, n) => onSelect(n.id)}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesConnectable={false}
        nodesDraggable={false}
        colorMode="light"
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
        maxZoom={1.6}
      >
        <Background color="#b8c2d3" gap={22} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
