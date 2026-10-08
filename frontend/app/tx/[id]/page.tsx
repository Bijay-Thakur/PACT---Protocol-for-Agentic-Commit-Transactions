"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTransactionLive } from "@/lib/useTransactionLive";
import { Card, ErrorBox, JsonView, Tabs } from "@/components/ui";
import { TxHeader } from "@/components/tx/TxHeader";
import { CommitBarrierPanel } from "@/components/tx/CommitBarrierPanel";
import { HierarchyPanel } from "@/components/tx/HierarchyPanel";
import { EffectDag } from "@/components/tx/EffectDag";
import { EffectInspector, pickDefaultEffect } from "@/components/tx/EffectInspector";
import { AuthorityPanel } from "@/components/tx/AuthorityPanel";
import { InvariantsPanel } from "@/components/tx/InvariantsPanel";
import { ExternalRealityPanel } from "@/components/tx/ExternalRealityPanel";
import { EventTimeline } from "@/components/tx/EventTimeline";
import { LiveKernel } from "@/components/site/Kernel";

type TabKey = "events" | "external" | "invariants" | "authority";

export default function TransactionPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  if (!id) return null;
  return <TransactionView key={id} id={id} />;
}

function TransactionView({ id }: { id: string }) {
  const { detail, events, error, mode, lastUpdate, refetch } = useTransactionLive(id);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<TabKey>("events");

  if (error && !detail) {
    return (
      <div className="space-y-3">
        <Link href="/transactions" className="text-xs text-mute hover:text-ink">
          ← all transactions
        </Link>
        <ErrorBox title={`Cannot load transaction ${id}`} message={`${error.code}: ${error.message}`} />
      </div>
    );
  }
  if (!detail) return <div className="text-sm text-faint">Loading transaction {id}…</div>;

  const selected = detail.effects.find((e) => e.id === selectedId) ?? pickDefaultEffect(detail.effects);
  const failedInv = detail.invariants.filter((i) => i.evaluations.length && !i.evaluations[i.evaluations.length - 1].passed).length;

  return (
    <div className="space-y-4">
      <Link href="/transactions" className="text-xs text-mute hover:text-ink">
        ← all transactions
      </Link>
      {error && <ErrorBox title="Refresh failed (showing last known state)" message={`${error.code}: ${error.message}`} />}
      <TxHeader detail={detail} mode={mode} lastUpdate={lastUpdate} onChanged={refetch} />
      <LiveKernel id={id} state={detail.transaction.state} events={events} />

      {(detail.plan_revision?.semantic_assessment || detail.plan_revision?.issues?.length) && (
        <Card title="Semantic review"
          subtitle="Advisory meaning checks cannot approve, execute, or override trusted policy. Concerns require attributable clarification.">
          <JsonView value={{
            disposition: detail.plan_revision.semantic_disposition,
            candidate_digest: detail.plan_revision.candidate_digest,
            assessment: detail.plan_revision.semantic_assessment,
            issues: detail.plan_revision.issues,
          }} maxHeight={340} />
        </Card>
      )}

      {detail.plan_revision?.digest && <Card title="Frozen consequence review"
        subtitle={`Projection/v1 and required outcomes are bound to digest ${detail.plan_revision.digest.slice(0, 16)}. Expected changes are projections until provider observations arrive.`}>
        <JsonView value={{ projection: detail.plan_revision.projection,
          required_outcomes: detail.plan_revision.required_outcomes }} maxHeight={340} />
      </Card>}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <CommitBarrierPanel detail={detail} />
        <HierarchyPanel detail={detail} events={events} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Card
          title="Effect DAG"
          subtitle="Execution order by dependency level. Dependents wait for prerequisites to be VERIFIED, not merely dispatched. Click a node to inspect."
        >
          <EffectDag detail={detail} selectedId={selected?.id ?? null} onSelect={setSelectedId} />
        </Card>
        <Card title="Effect inspector" subtitle="What the provider said vs. what independent verification found">
          <EffectInspector effect={selected} />
        </Card>
      </div>

      <section className="neu-raised">
        <div className="px-5 pt-5">
          <Tabs<TabKey>
            active={tab}
            onChange={setTab}
            tabs={[
              { key: "events", label: `Event timeline (${events.length})` },
              { key: "external", label: "External reality" },
              { key: "invariants", label: `Invariants${failedInv ? ` · ${failedInv} failed` : ""}` },
              { key: "authority", label: "Authority" },
            ]}
          />
        </div>
        <div className="p-4">
          {tab === "events" && <EventTimeline events={events} detail={detail} />}
          {tab === "external" && <ExternalRealityPanel customerId={detail.metadata.customer_id}
            transactionId={detail.root_id} refreshKey={detail.event_count} />}
          {tab === "invariants" && <InvariantsPanel detail={detail} />}
          {tab === "authority" && <AuthorityPanel detail={detail} />}
        </div>
      </section>
    </div>
  );
}
