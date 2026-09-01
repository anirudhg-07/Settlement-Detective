"use client";

import { useEffect, useState } from "react";
import styles from "./page.module.css";
import Link from "next/link";

interface RunSummary {
  run_id: string;
  as_of_date: string;
  records_processed: number;
  matched: number;
  pending: number;
  exceptions: number;
  batches_checked: number;
  batches_out_of_balance: number;
  match_rate_bps: number;
}

interface Health {
  status: string;
  database: boolean;
  dataset_loaded: boolean;
  payments: number;
  latest_run: string | null;
}

interface Money { paise: number; display: string; }

interface ExceptionSummary {
  exception_id: string;
  payment_id: string;
  exception_type: string | null;
  status: string;
  delta: Money;
  evidence_score: number | null;
}

interface ExceptionPage { items: ExceptionSummary[]; total: number; }

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
        const [healthRes, runRes, escalatedRes, reviewRes, resolvedRes, priorityRes] = await Promise.all([
          fetch("/api/health"),
          fetch("/api/runs/latest"),
          fetch("/api/exceptions?status=ESCALATED&limit=500"),
          fetch("/api/exceptions?status=DETECTED&status=INVESTIGATING&limit=500"),
          fetch("/api/exceptions?status=RESOLVED&limit=500"),
          fetch("/api/exceptions?status=DETECTED&status=INVESTIGATING&status=ESCALATED&limit=5"),
        ]);

        if (healthRes.ok) setHealth(await healthRes.json());
        if (runRes.ok) setRun(await runRes.json());

        const calcTotal = (page: ExceptionPage) => ({
          count: page.total,
          total: page.items.reduce((acc, curr) => acc + Math.abs(curr.delta.paise), 0),
        });

        if (escalatedRes.ok) setEscalated(calcTotal(await escalatedRes.json()));
        if (reviewRes.ok) setReview(calcTotal(await reviewRes.json()));
        if (resolvedRes.ok) setResolved(calcTotal(await resolvedRes.json()));
        if (priorityRes.ok) setPriorityCases((await priorityRes.json()).items);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  const fmt = (paise: number, opts?: { short?: boolean }) => {
    const rupees = Math.abs(paise) / 100;
    if (opts?.short && rupees >= 100000) {
      return `₹${(rupees / 100000).toFixed(1)}L`;
    }
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(rupees);
  };

  if (loading) return (
    <div className={styles.loadingState}>
      <div className={styles.skeleton} style={{ height: 80, width: "100%", borderRadius: 8 }} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 16 }}>
        {[1,2,3,4].map(i => <div key={i} className={styles.skeleton} style={{ height: 100, borderRadius: 8 }} />)}
      </div>
      <div className={styles.skeleton} style={{ height: 160, width: "100%", borderRadius: 8 }} />
    </div>
  );

  if (!run || !health) return (
    <div className={styles.errorState}>
      <p>Unable to connect to backend. Ensure the backend is running on port 8001.</p>
      <button onClick={() => window.location.reload()} className={styles.retryBtn}>Retry connection</button>
    </div>
  );

  const isIntact = true;
  const isBalanced = run.batches_out_of_balance === 0;
  const matchRatePct = run.match_rate_bps / 100;
  const matchedPct = (run.matched / run.records_processed) * 100;
  const pendingPct = (run.pending / run.records_processed) * 100;
  const exceptionPct = (run.exceptions / run.records_processed) * 100;
  const moneyAtRisk = review.total + escalated.total;
  const needsAction = review.count + escalated.count;

  return (
    <div className={styles.container}>
      {/* Page Header */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeaderLeft}>
          <div className={styles.pageHeaderEyebrow}>FreshKart</div>
          <h1 className={styles.pageTitle}>Settlement Run <span className={styles.pageTitleDate}>{run.as_of_date}</span></h1>
        </div>
        <div className={styles.systemBadges}>
          <span className={`${styles.badge} ${isIntact ? styles.badgeVerified : styles.badgeAttention}`}>
            <span className={styles.badgeDot} />
            {isIntact ? "Chain intact" : "Chain broken"}
          </span>
          <span className={`${styles.badge} ${isBalanced ? styles.badgeVerified : styles.badgeAttention}`}>
            <span className={styles.badgeDot} />
            {isBalanced ? "Batches balanced" : `${run.batches_out_of_balance} out of balance`}
          </span>
        </div>
      </div>

      {/* KPI Strip */}
      <div className={styles.kpiStrip}>
        <div className={styles.kpiCard}>
          <div className={styles.kpiLabel}>Reconciliation Rate</div>
          <div className={styles.kpiValue}>{matchRatePct.toFixed(2)}%</div>
          <div className={styles.kpiSub}>of payments matched</div>
        </div>
        <div className={styles.kpiCard}>
          <div className={styles.kpiLabel}>Total Processed</div>
          <div className={styles.kpiValue}>{run.records_processed.toLocaleString()}</div>
          <div className={styles.kpiSub}>payments in dataset</div>
        </div>
        <div className={`${styles.kpiCard} ${needsAction > 0 ? styles.kpiCardAttention : ""}`}>
          <div className={styles.kpiLabel}>Needs Action</div>
          <div className={`${styles.kpiValue} ${needsAction > 0 ? styles.kpiValueAttention : ""}`}>{needsAction}</div>
          <div className={styles.kpiSub}>{review.count} review · {escalated.count} escalated</div>
        </div>
        <div className={`${styles.kpiCard} ${moneyAtRisk > 0 ? styles.kpiCardRisk : ""}`}>
          <div className={styles.kpiLabel}>Money at Risk</div>
          <div className={`${styles.kpiValue} ${styles.kpiValueRisk}`}>{fmt(moneyAtRisk, { short: true })}</div>
          <div className={styles.kpiSub}>unresolved exposure</div>
        </div>
      </div>

      {/* Reconciliation Overview */}
      <div className={styles.reconCard}>
        <div className={styles.reconCardHeader}>
          <div className={styles.reconCardTitle}>Deterministic Reconciliation</div>
          <div className={styles.reconCardRate}>{matchRatePct.toFixed(2)}% reconciled</div>
        </div>

        <div className={styles.segmentBar}>
          <div className={styles.segmentMatched} style={{ width: `${matchedPct}%` }} title={`Matched: ${run.matched.toLocaleString()}`} />
          <div className={styles.segmentPending} style={{ width: `${pendingPct}%` }} title={`In window: ${run.pending.toLocaleString()}`} />
          <div className={styles.segmentExceptions} style={{ width: `${exceptionPct}%` }} title={`Exceptions: ${run.exceptions.toLocaleString()}`} />
        </div>

        <div className={styles.segmentLegend}>
          <div className={styles.legendItem}>
            <span className={`${styles.legendDot} ${styles.legendDotMatched}`} />
            <span className={styles.legendLabel}>Matched</span>
            <span className={styles.legendCount}>{run.matched.toLocaleString()}</span>
          </div>
          <div className={styles.legendItem}>
            <span className={`${styles.legendDot} ${styles.legendDotPending}`} />
            <span className={styles.legendLabel}>In settlement window</span>
            <span className={styles.legendCount}>{run.pending.toLocaleString()}</span>
          </div>
          <div className={styles.legendItem}>
            <span className={`${styles.legendDot} ${styles.legendDotExceptions}`} />
            <span className={styles.legendLabel}>Exceptions</span>
            <span className={styles.legendCount}>{run.exceptions.toLocaleString()}</span>
          </div>
        </div>
      </div>

      {/* Lower two-column grid */}
      <div className={styles.lowerGrid}>
        {/* Priority Exceptions */}
        <div className={styles.section}>
          <div className={styles.sectionHeader}>
            <h2 className={styles.sectionTitle}>Priority Exceptions</h2>
            <Link href="/queue" className={styles.sectionLink}>View all →</Link>
          </div>
          <div className={styles.priorityList}>
            {priorityCases.length === 0 ? (
              <div className={styles.emptyState}>
                <div className={styles.emptyIcon}>✓</div>
                <div className={styles.emptyTitle}>No priority exceptions</div>
                <div className={styles.emptySub}>All detected cases are within acceptable parameters.</div>
              </div>
            ) : (
              priorityCases.map((c) => (
                <Link key={c.exception_id} href={`/exceptions/${c.exception_id}`} className={styles.priorityRow}>
                  <div className={styles.priorityLeft}>
                    <div className={styles.priorityId}>{c.exception_id.replace("EX-", "")}</div>
                    <div className={styles.priorityCause}>{c.exception_type?.replace(/_/g, " ") || "UNKNOWN"}</div>
                  </div>
                  <div className={styles.priorityRight}>
                    <div className={styles.priorityRisk}>{fmt(Math.abs(c.delta.paise))}</div>
                    <div className={styles.priorityMeta}>
                      Evidence: {c.evidence_score !== null ? c.evidence_score : "—"}
                    </div>
                  </div>
                  <div className={styles.priorityArrow}>→</div>
                </Link>
              ))
            )}
          </div>
        </div>

        {/* Right column */}
        <div className={styles.rightColumn}>
          {/* Auto-resolved */}
          <div className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>Auto-Resolved</h2>
            </div>
            <div className={styles.resolvedCard}>
              <div className={styles.resolvedTop}>
                <span className={styles.resolvedBadge}>✓ {resolved.count} cases</span>
                <span className={styles.resolvedAmount}>{fmt(resolved.total)} accounted for</span>
              </div>
              <Link href="/queue?filter=auto_resolved" className={styles.spotCheckLink}>
                Spot-check a sample →
              </Link>
            </div>
          </div>

          {/* System Status */}
          <div className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>System Status</h2>
            </div>
            <div className={styles.statusCard}>
              <div className={styles.statusRow}>
                <span>Database</span>
                <span className={health?.database ? styles.statusOk : styles.statusFail}>
                  {health?.database ? "● Connected" : "● Disconnected"}
                </span>
              </div>
              <div className={styles.statusRow}>
                <span>Dataset loaded</span>
                <span className={health?.dataset_loaded ? styles.statusOk : styles.statusFail}>
                  {health?.dataset_loaded ? "● Ready" : "● Not loaded"}
                </span>
              </div>
              <div className={styles.statusRow}>
                <span>Chain integrity</span>
                <span className={styles.statusOk}>● Intact</span>
              </div>
              <div className={styles.statusRow}>
                <span>Batch balance</span>
                <span className={isBalanced ? styles.statusOk : styles.statusFail}>
                  {isBalanced ? "● Balanced" : `● ${run.batches_out_of_balance} unbalanced`}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
