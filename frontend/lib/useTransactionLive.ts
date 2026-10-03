"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import type { PactEvent, TransactionDetail } from "./types";

export type LiveMode = "connecting" | "sse" | "polling";

/**
 * Loads a transaction detail and its durable event log, and keeps both live:
 * - SSE on /events/stream (replays from sequence 0, then tails). Each new event
 *   triggers a debounced (300ms) refetch of the detail read model.
 * - If EventSource errors, falls back to polling detail + events every 2s.
 */
export function useTransactionLive(id: string | undefined) {
  const [detail, setDetail] = useState<TransactionDetail | null>(null);
  const [events, setEvents] = useState<PactEvent[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [mode, setMode] = useState<LiveMode>("connecting");
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);

  const seqRef = useRef(0);
  const seenRef = useRef<Set<number>>(new Set());
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchDetail = useCallback(async () => {
    if (!id) return;
    try {
      const d = await api.getTransaction(id);
      setDetail(d);
      setError(null);
      setLastUpdate(Date.now());
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "UNKNOWN", String(e), null));
    }
  }, [id]);

  const addEvents = useCallback((batch: PactEvent[]) => {
    const fresh = batch.filter((e) => !seenRef.current.has(e.sequence));
    if (!fresh.length) return false;
    for (const e of fresh) {
      seenRef.current.add(e.sequence);
      if (e.sequence > seqRef.current) seqRef.current = e.sequence;
    }
    setEvents((prev) => [...prev, ...fresh].sort((a, b) => a.sequence - b.sequence));
    return true;
  }, []);

  const scheduleRefetch = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      void fetchDetail();
    }, 300);
  }, [fetchDetail]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let es: EventSource | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    // Note: callers remount (key={id}) per transaction, so state starts fresh.
    const initial = setTimeout(() => void fetchDetail(), 0);

    const startPolling = () => {
      if (pollTimer || cancelled) return;
      setMode("polling");
      const tick = async () => {
        try {
          const { events: batch } = await api.getEvents(id, seqRef.current);
          if (!cancelled) addEvents(batch);
        } catch {
          /* surfaced via detail fetch */
        }
        if (!cancelled) await fetchDetail();
      };
      void tick();
      pollTimer = setInterval(tick, 2000);
    };

    if (typeof window !== "undefined" && "EventSource" in window) {
      es = new EventSource(api.eventStreamUrl(id, 0));
      es.addEventListener("open", () => {
        if (!cancelled) setMode("sse");
      });
      es.addEventListener("pact", (msg) => {
        if (cancelled) return;
        try {
          const ev = JSON.parse((msg as MessageEvent).data) as PactEvent;
          if (addEvents([ev])) scheduleRefetch();
        } catch {
          /* ignore malformed frame */
        }
      });
      es.addEventListener("error", () => {
        es?.close();
        es = null;
        startPolling();
      });
    } else {
      startPolling();
    }

    return () => {
      cancelled = true;
      clearTimeout(initial);
      es?.close();
      if (pollTimer) clearInterval(pollTimer);
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [id, fetchDetail, addEvents, scheduleRefetch]);

  return { detail, events, error, mode, lastUpdate, refetch: fetchDetail };
}
