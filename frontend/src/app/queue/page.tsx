"use client";

import { useEffect, useState, Suspense } from "react";
import Link from "next/link";
import styles from "./page.module.css";
import { useRouter, useSearchParams } from "next/navigation";

interface Money { paise: number; display: string; }

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

interface ExceptionPage { items: ExceptionSummary[]; total: number; }

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    RESOLVED:     { label: "Resolved",     cls: styles.badgeVerified },
    ESCALATED:    { label: "Escalated",    cls: styles.badgeAttention },
    REJECTED:     { label: "Rejected",     cls: styles.badgeNegative },
    DETECTED:     { label: "Detected",     cls: styles.badgeNeutral },
    INVESTIGATING:{ label: "Investigating", cls: styles.badgeNeutral },
    REVIEW:       { label: "Review",       cls: styles.badgeAttention },
  };
  const cfg = map[status] || { label: status, cls: styles.badgeNeutral };
  return <span className={`${styles.badge} ${cfg.cls}`}><span className={styles.badgeDot} />{cfg.label}</span>;
}

function QueueContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const filter = searchParams.get("filter") || "needs_you";

  const [page, setPage] = useState<ExceptionPage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      setLoading(true);
      try {
        let url = "/api/exceptions?limit=100";
        if (filter === "needs_you") url += "&status=DETECTED&status=INVESTIGATING&status=ESCALATED";
        else if (filter === "auto_resolved") url += "&status=RESOLVED";
        const res = await fetch(url);
        if (res.ok) setPage(await res.json());
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [filter]);

  const setFilter = (f: string) => router.push(`/queue?filter=${f}`);

  const fmt = (paise: number) => {
    if (paise === 0) return "₹0";
    const r = Math.abs(paise) / 100;
    return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(r);
  };

  const getWhyText = (type: string | null) => {
    if (!type) return "Awaiting investigation";
    if (type.includes("MISSING_REFUND")) return "Refund expected but not debited from payout";
    if (type.includes("MISSING_SETTLEMENT")) return "Payment captured but settlement never arrived";
    if (type.includes("FEE")) return "Charged fee differs from expected rate";
    if (type.includes("DUPLICATE")) return "Multiple settlement lines for one capture";
    return "Discrepancy flagged by reconciliation engine";
  };

  const tabs = [
    { id: "needs_you", label: "Needs You", dot: styles.dotAttention },
    { id: "auto_resolved", label: "Auto-resolved", dot: styles.dotVerified },
    { id: "all", label: "All" },
  ];

  return (
    <>
      <div className={styles.toolbar}>
        <div className={styles.tabs}>
          {tabs.map(t => (
            <button
              key={t.id}
              onClick={() => setFilter(t.id)}
              className={`${styles.tab} ${filter === t.id ? styles.tabActive : ""}`}
            >
              {t.dot && <span className={`${styles.tabDot} ${t.dot}`} />}
              {t.label}
              {page && filter === t.id && (
                <span className={styles.tabCount}>{page.total}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className={styles.tableWrapper}>
        {loading ? (
          <div className={styles.tableLoading}>
            {[1,2,3,4,5].map(i => (
              <div key={i} className={styles.skeletonRow}>
                <div className={styles.skeleton} style={{ width: 100 }} />
                <div className={styles.skeleton} style={{ width: 140 }} />
                <div className={styles.skeleton} style={{ width: 80 }} />
                <div className={styles.skeleton} style={{ width: 60 }} />
                <div className={styles.skeleton} style={{ flex: 1 }} />
                <div className={styles.skeleton} style={{ width: 80 }} />
              </div>
            ))}
          </div>
        ) : !page || page.items.length === 0 ? (
          <div className={styles.emptyState}>
            <div className={styles.emptyIcon}>
              {filter === "needs_you" ? "✓" : "—"}
            </div>
            <div className={styles.emptyTitle}>
              {filter === "needs_you" ? "Queue is clear" : "No cases match this filter"}
            </div>
            <div className={styles.emptySub}>
              {filter === "needs_you"
                ? "All exceptions have been reviewed or auto-resolved."
                : "Try switching to a different tab."}
            </div>
          </div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>CASE</th>
                <th>CAUSE</th>
                <th className={styles.right}>EXPOSURE</th>
                <th className={styles.right}>EVIDENCE</th>
                <th>WHY</th>
                <th>STATUS</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map(item => (
                <tr
                  key={item.exception_id}
                  className={styles.row}
                  onClick={() => router.push(`/exceptions/${item.exception_id}`)}
                >
                  <td>
                    <div className={styles.caseId}>{item.exception_id.replace("EX-", "")}</div>
                    <div className={styles.paymentId}>{item.payment_id?.slice(0, 16)}</div>
                  </td>
                  <td>
                    <span className={styles.causeTag}>
                      {item.exception_type?.replace(/_/g, " ") || "UNKNOWN"}
                    </span>
                  </td>
                  <td className={`${styles.right} ${styles.exposureCell}`}>
                    {item.delta.paise !== 0 ? (
                      <span className={Math.abs(item.delta.paise) > 100000 ? styles.exposureLarge : styles.exposureNormal}>
                        {fmt(item.delta.paise)}
                      </span>
                    ) : (
                      <span className={styles.exposureZero}>₹0</span>
                    )}
                  </td>
                  <td className={`${styles.right} ${styles.evidenceCell}`}>
                    {item.evidence_score === null ? (
                      <span className={styles.evidenceNone}>—</span>
                    ) : item.evidence_score === 0 ? (
                      <span className={styles.evidenceLow}>0</span>
                    ) : (
                      <span className={styles.evidenceOk}>{item.evidence_score}</span>
                    )}
                  </td>
                  <td className={styles.whyCell}>{getWhyText(item.exception_type)}</td>
                  <td><StatusBadge status={item.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {page && page.total > page.items.length && (
        <div className={styles.paginationNote}>
          Showing {page.items.length} of {page.total.toLocaleString()} — prioritized by financial exposure
        </div>
      )}
    </>
  );
}

export default function QueuePage() {
  return (
    <div className={styles.container}>
      <div className={styles.pageHeader}>
        <div>
          <h1 className={styles.pageTitle}>Exception Queue</h1>
          <p className={styles.pageSubtitle}>Prioritized by financial exposure · Click any case to investigate</p>
        </div>
      </div>
      <div className={styles.queueCard}>
        <Suspense fallback={<div className={styles.suspenseFallback}>Loading queue...</div>}>
          <QueueContent />
        </Suspense>
      </div>
    </div>
  );
}
