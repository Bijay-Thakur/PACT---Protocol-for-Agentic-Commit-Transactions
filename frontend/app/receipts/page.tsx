import { TransactionList } from "@/components/TransactionList";

export default function ReceiptsPage() {
  return (
    <div className="space-y-4">
      <div className="neu-inset rounded-xl px-5 py-4 text-sm text-mute">
        Open a terminal transaction to inspect its immutable receipt, verification hash, observed outcomes,
        residual obligations, and amendment chain.
      </div>
      <TransactionList />
    </div>
  );
}
