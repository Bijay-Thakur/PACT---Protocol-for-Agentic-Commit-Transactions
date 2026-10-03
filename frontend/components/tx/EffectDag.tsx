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

const COL_W = 228;
const ROW_H = 96;

function EffectNodeView({ data }: NodeProps<EffectNode>) {
  const e = data.effect;
  const c = stateClasses(e.state);
  const dispatch = e.dispatch_result?.outcome;
  const verify = e.verification_result?.status;
  return (
    <div
      className={`w-[196px] rounded-md border-2 bg-zinc-900 px-2.5 py-1.5 text-left shadow ${c.border} ${
        data.selected ? "ring-2 ring-white/80" : ""
      } ${isInFlight(e.state) ? "animate-pulse" : ""}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-zinc-500" />
      <div className="font-mono text-[12px] font-semibold text-zinc-100">{e.effect_type}</div>
      <div className="font-mono text-[10px] text-zinc-400">
        {e.actor_id}
        {e.amount ? ` · ${money(e.amount)}` : ""}
      </div>
      <div className={`mt-0.5 font-mono text-[11px] font-semibold ${c.text}`}>{e.state}</div>
      {(dispatch || verify) && (
        <div className="mt-0.5 flex gap-1 font-mono text-[9px]">
          {dispatch && <span className={stateClasses(dispatch).text}>said:{dispatch}</span>}
          {verify && <span className={stateClasses(verify).text}>real:{verify.replace("VERIFIED_", "")}</span>}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-zinc-500" />
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
      const color = src ? stateClasses(src.state).hex : "#71717a";
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
    return <div className="p-6 text-sm text-zinc-500">No effects proposed in this transaction tree.</div>;
  }

  return (
    <div style={{ height }} className="w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={(_, n) => onSelect(n.id)}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesConnectable={false}
        nodesDraggable={false}
        colorMode="dark"
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
        maxZoom={1.6}
      >
        <Background color="#27272a" gap={20} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
