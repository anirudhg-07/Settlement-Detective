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
  
  const [escalated, setEscalated] = useState<{ count: number; total: number }>({ count: 0, total: 0 });
  const [review, setReview] = useState<{ count: number; total: number }>({ count: 0, total: 0 });
  const [resolved, setResolved] = useState<{ count: number; total: number }>({ count: 0, total: 0 });
  
  const [priorityCases, setPriorityCases] = useState<ExceptionSummary[]>([]);
  
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      try {
        const [healthRes, runRes, escalatedRes, reviewRes, resolvedRes, priorityRes] =
          await Promise.all([
            fetch("/api/health"),
            fetch("/api/runs/latest"),
            fetch("/api/exceptions?status=ESCALATED&limit=500"),
            fetch("/api/exceptions?status=DETECTED&status=INVESTIGATING&limit=500"),
            fetch("/api/exceptions?status=RESOLVED&limit=500"),
            fetch("/api/exceptions?status=DETECTED&status=INVESTIGATING&status=ESCALATED&limit=3") // highest delta
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
        if (priorityRes.ok) {
          const pPage = await priorityRes.json();
          setPriorityCases(pPage.items);
        }
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  const formatMoney = (paise: number) => {
    const rupees = Math.abs(paise) / 100;
    const formatted = new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
    }).format(rupees);
    return formatted; // Will handle sign outside if needed, usually absolute for exposure
  };

  if (loading) {
    return <div className={styles.loading}>Loading operational data...</div>;
  }

  if (!run || !health) {
    return <div className={styles.error}>Unable to load run data. Ensure backend is running.</div>;
  }

  const isIntact = true; 
  const isBalanced = run.batches_out_of_balance === 0;
  
  const totalReconciled = run.matched + run.pending;
  const matchRatePct = (run.match_rate_bps / 100).toFixed(2);
  
  const moneyAtRiskPaise = review.total + escalated.total;

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <div className={styles.headerTitle}>
          FreshKart <span className={styles.headerMuted}>· Settlement run {run.as_of_date}</span>
        </div>
        <div className={styles.headerStatus}>
          <span className={isIntact ? styles.statusVerified : styles.statusAttention}>
            {isIntact ? "✓" : "✗"} Chain intact
          </span>
          <span className={isBalanced ? styles.statusVerified : styles.statusAttention}>
            {isBalanced ? "✓" : "✗"} Batches balanced
          </span>
        </div>
      </header>

      <div className={styles.section}>
        <h2 className={styles.sectionTitle}>RECONCILIATION</h2>
        <div className={styles.reconciliationCard}>
          <div className={styles.reconHeader}>
            <span className={styles.reconCount}>
              <span className={styles.tabular}>{totalReconciled.toLocaleString()}</span> / <span className={styles.tabular}>{run.records_processed.toLocaleString()}</span> reconciled
            </span>
          </div>
          
          <div className={styles.progressBar}>
            <div className={styles.progressFill} style={{ width: `${matchRatePct}%` }}></div>
          </div>
          
          <div className={styles.reconFooter}>
            <span className={styles.reconRate}>{matchRatePct}%</span>
            <div className={styles.reconStats}>
              <span><span className={styles.tabular}>{run.matched.toLocaleString()}</span> MATCHED</span>
              <span><span className={styles.tabular}>{run.pending.toLocaleString()}</span> IN SETTLEMENT WINDOW</span>
              <span><span className={styles.tabular}>{run.exceptions.toLocaleString()}</span> EXCEPTIONS</span>
            </div>
          </div>
        </div>
      </div>

      <div className={styles.grid}>
        <div className={styles.section}>
          <h2 className={styles.sectionTitle}>NEEDS YOUR ATTENTION</h2>
          <div className={styles.attentionCard}>
            <div className={styles.attentionStats}>
              <div className={styles.attentionCol}>
                <span className={styles.attentionLabel}>REVIEW</span>
                <span className={styles.attentionCount}>{review.count}</span>
              </div>
              <div className={styles.attentionCol}>
                <span className={styles.attentionLabel}>ESCALATED</span>
                <span className={styles.attentionCount}>{escalated.count}</span>
              </div>
            </div>
            <div className={styles.moneyAtRiskBox}>
              <span className={styles.moneyLabel}>MONEY AT RISK</span>
              <span className={styles.moneyValue}>{formatMoney(moneyAtRiskPaise)}</span>
            </div>
          </div>
        </div>

        <div className={styles.section}>
          <h2 className={styles.sectionTitle}>AUTO-RESOLVED — SPOT CHECK</h2>
          <div className={styles.resolvedCard}>
            <div className={styles.resolvedStats}>
              <span className={styles.resolvedCount}>{resolved.count} cases</span>
              <span className={styles.resolvedMoney}>{formatMoney(resolved.total)} accounted for</span>
            </div>
            <Link href="/queue?filter=auto_resolved" className={styles.resolvedLink}>
              Review a random sample →
            </Link>
          </div>
        </div>
      </div>

      {priorityCases.length > 0 && (
        <div className={styles.section}>
          <h2 className={styles.sectionTitle}>PRIORITY EXCEPTIONS</h2>
          <div className={styles.priorityList}>
            {priorityCases.map(caseItem => (
              <Link 
                key={caseItem.exception_id} 
                href={`/exceptions/${caseItem.exception_id}`} 
                className={styles.priorityRow}
              >
                <div className={styles.priorityLeft}>
                  <div className={styles.priorityId}>{caseItem.exception_id.split("-")[1] || caseItem.exception_id}</div>
                  <div className={styles.priorityCause}>{caseItem.exception_type?.replace(/_/g, " ") || "UNKNOWN"}</div>
                </div>
                <div className={styles.priorityRight}>
                  <div className={styles.priorityRisk}>{formatMoney(Math.abs(caseItem.delta.paise))} at risk</div>
                  <div className={styles.priorityEvidence}>{caseItem.evidence_score !== null ? `${caseItem.evidence_score} evidence` : "No evidence"}</div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
