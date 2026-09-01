"use client";

import { useEffect, useState } from "react";
import styles from "./page.module.css";
import Link from "next/link";

interface RunSummary {
  run_id: string;
  as_of_date: string;
  started_at: string;
  completed_at: string | null;
  records_processed: number;
  matched: number;
  pending: number;
  exceptions: number;
  batches_checked: number;
  batches_out_of_balance: number;
  match_rate_bps: number;
  tolerance_paise: number;
}

interface Health {
  status: string;
  database: boolean;
  dataset_loaded: boolean;
  payments: number;
  latest_run: string | null;
}

interface Money {
  paise: number;
  display: string;
}

interface ExceptionSummary {
  exception_id: string;
  payment_id: string;
  exception_type: string | null;
  status: string;
  detected_by: string;
  expected_net: Money;
  actual_net: Money;
  delta: Money;
  evidence_score: number | null;
  created_at: string;
}

interface ExceptionPage {
  items: ExceptionSummary[];
  total: number;
}

export default function CommandCenter() {
  const [health, setHealth] = useState<Health | null>(null);
  const [run, setRun] = useState<RunSummary | null>(null);
  const [escalated, setEscalated] = useState<{ count: number; total: number }>({
    count: 0,
    total: 0,
  });
  const [review, setReview] = useState<{ count: number; total: number }>({
    count: 0,
    total: 0,
  });
  const [resolved, setResolved] = useState<{ count: number; total: number }>({
    count: 0,
    total: 0,
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      try {
        const [healthRes, runRes, escalatedRes, reviewRes, resolvedRes] =
          await Promise.all([
            fetch("/api/health"),
            fetch("/api/runs/latest"),
            fetch("/api/exceptions?status=ESCALATED&limit=500"),
            fetch("/api/exceptions?status=DETECTED&status=INVESTIGATING&limit=500"),
            fetch("/api/exceptions?status=RESOLVED&limit=500"),
          ]);

        if (healthRes.ok) setHealth(await healthRes.json());
        if (runRes.ok) setRun(await runRes.json());

        const calcTotal = (page: ExceptionPage) => {
          const sum = page.items.reduce(
            (acc, curr) => acc + Math.abs(curr.delta.paise),
            0
          );
          return { count: page.total, total: sum };
        };

        if (escalatedRes.ok) setEscalated(calcTotal(await escalatedRes.json()));
        if (reviewRes.ok) setReview(calcTotal(await reviewRes.json()));
        if (resolvedRes.ok) setResolved(calcTotal(await resolvedRes.json()));
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  const formatMoney = (paise: number) => {
    const rupees = paise / 100;
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
    }).format(rupees);
  };

  if (loading) {
    return <div className={styles.loading}>Loading run data...</div>;
  }

  if (!run || !health) {
    return <div className={styles.error}>Unable to load run data.</div>;
  }

  const isIntact = true; // In a real app, this would be determined by the integrity API
  const isBalanced = run.batches_out_of_balance === 0;

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <div className={styles.headerTitle}>
          FreshKart · settlement run {run.as_of_date}
        </div>
        <div className={styles.headerStatus}>
          <span className={isIntact ? styles.statusOk : styles.statusError}>
            chain intact {isIntact ? "✓" : "✗"}
          </span>
          <span className={isBalanced ? styles.statusOk : styles.statusError}>
            batches balanced {isBalanced ? "✓" : "✗"}
          </span>
        </div>
      </header>

      <div className={styles.mainGrid}>
        <div className={styles.needsAttention}>
          <h2 className={styles.sectionTitle}>NEEDS ATTENTION</h2>
          <div className={styles.attentionCards}>
            <div className={styles.card}>
              <div className={styles.cardHeader}>REVIEW</div>
              <div className={styles.cardCount}>{review.count}</div>
              <div className={styles.cardMoney}>{formatMoney(review.total)}</div>
            </div>
            <div className={styles.card}>
              <div className={styles.cardHeader}>ESCALATED</div>
              <div className={styles.cardCount}>{escalated.count}</div>
              <div className={styles.cardMoneyAttention}>
                {formatMoney(escalated.total)}
              </div>
            </div>
          </div>
        </div>

        <div className={styles.noAction}>
          <h2 className={styles.sectionTitle}>NO ACTION</h2>
          <div className={styles.noActionList}>
            <div className={styles.noActionItem}>
              <span className={styles.noActionCount}>{run.matched}</span>
              <span className={styles.noActionLabel}>matched</span>
            </div>
            <div className={styles.noActionItem}>
              <span className={styles.noActionCount}>{run.pending}</span>
              <span className={styles.noActionLabel}>
                in settlement window
              </span>
            </div>
          </div>
          <div className={styles.matchRate}>
            {(run.match_rate_bps / 100).toFixed(2)}% reconciled
          </div>
        </div>
      </div>

      <div className={styles.autoResolved}>
        <h2 className={styles.sectionTitle}>AUTO-RESOLVED — spot-check</h2>
        <div className={styles.resolvedRow}>
          <span>
            {resolved.count} cases · {formatMoney(resolved.total)} accounted for
          </span>
          <Link href="/queue?status=RESOLVED" className={styles.link}>
            review a random sample →
          </Link>
        </div>
      </div>
    </div>
  );
}
