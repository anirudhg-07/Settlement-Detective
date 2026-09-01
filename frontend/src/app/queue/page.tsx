"use client";

import { useEffect, useState, Suspense } from "react";
import Link from "next/link";
import styles from "./page.module.css";
import { useRouter, useSearchParams } from "next/navigation";

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
        let url = "/api/exceptions?limit=50";
        if (filter === "needs_you") {
          url += "&status=DETECTED&status=INVESTIGATING&status=ESCALATED";
        } else if (filter === "auto_resolved") {
          url += "&status=RESOLVED";
        }

        const res = await fetch(url);
        if (res.ok) {
          setPage(await res.json());
        }
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [filter]);

  const setFilter = (newFilter: string) => {
    router.push(`/queue?filter=${newFilter}`);
  };

  const formatMoney = (paise: number) => {
    const rupees = Math.abs(paise) / 100;
    const formatted = new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
    }).format(rupees);
    return paise < 0 ? `−${formatted}` : `+${formatted}`;
  };

  const getStatusNode = (status: string) => {
    let className = styles.statusNeutral;
    let label = status;
    switch (status) {
      case "RESOLVED": 
        className = styles.statusVerified;
        label = "RESOLVED ✓";
        break;
      case "ESCALATED": 
        className = styles.statusAttention;
        label = "ESCALATED ↑";
        break;
      case "REVIEW":
      case "DETECTED":
      case "INVESTIGATING":
        className = styles.statusAttention;
        break;
      case "REJECTED": 
        className = styles.statusNegative;
        label = "REJECTED ✗";
        break;
    }
    return <span className={`${styles.statusBadge} ${className}`}>{label}</span>;
  };

  // Polyfill for missing "decision" in summary API
  const getWhyText = (type: string | null) => {
    if (!type) return "Awaiting investigation.";
    if (type.includes("MISSING_REFUND")) return "Refund was expected but never debited from payout.";
    if (type.includes("MISSING_SETTLEMENT")) return "Payment captured but settlement never arrived.";
    if (type.includes("FEE")) return "Charged fee differs from expected standard rate.";
    if (type.includes("DUPLICATE")) return "Multiple settlement lines exist for one capture.";
    return "Reconciliation engine flagged a discrepancy.";
  };

  return (
    <>
      <div className={styles.header}>
        <div className={styles.tabs}>
          <button
            onClick={() => setFilter("needs_you")}
            className={`${styles.tab} ${filter === "needs_you" ? styles.tabActive : ""}`}
          >
            Needs you
          </button>
          <button
            onClick={() => setFilter("auto_resolved")}
            className={`${styles.tab} ${filter === "auto_resolved" ? styles.tabActive : ""}`}
          >
            Auto-resolved
          </button>
          <button
            onClick={() => setFilter("all")}
            className={`${styles.tab} ${filter === "all" ? styles.tabActive : ""}`}
          >
            All
          </button>
        </div>
      </div>

      <div className={styles.tableContainer}>
        {loading ? (
          <div className={styles.loading}>Loading operational queue...</div>
        ) : !page || page.items.length === 0 ? (
          <div className={styles.empty}>
            {filter === "needs_you"
              ? "Queue empty. No cases are waiting for your review."
              : "No cases match these filters."}
          </div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>CASE</th>
                <th>CAUSE</th>
                <th className={styles.rightAlign}>AT RISK</th>
                <th className={styles.rightAlign}>CONFIDENCE</th>
                <th>WHY</th>
                <th>STATUS</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr key={item.exception_id} className={styles.row}>
                  <td>
                    <Link
                      href={`/exceptions/${item.exception_id}`}
                      className={styles.caseLink}
                    >
                      {item.exception_id.split("-")[1] || item.exception_id}
                    </Link>
                  </td>
                  <td className={styles.causeCell}>
                    {item.exception_type?.replace(/_/g, " ") || "UNKNOWN"}
                  </td>
                  <td className={`${styles.rightAlign} ${styles.tabular} ${
                    item.delta.paise < 0 ? styles.negative : styles.attention
                  }`}>
                    {item.delta.paise === 0 ? "₹0.00" : formatMoney(item.delta.paise)}
                  </td>
                  <td className={`${styles.rightAlign} ${styles.tabular} ${styles.confCell}`}>
                    {item.evidence_score === 0 ? "0 — System declined" : (item.evidence_score !== null ? item.evidence_score : "—")}
                  </td>
                  <td className={styles.whyCell}>
                    {getWhyText(item.exception_type)}
                  </td>
                  <td className={styles.statusCell}>
                    {getStatusNode(item.status)}
                  </td>
                  
                  {/* Absolute overlay link to make entire row clickable without nesting <a> tags invalidly */}
                  <td className={styles.rowLinkOverlay}>
                    <Link href={`/exceptions/${item.exception_id}`} className={styles.hiddenLink} tabIndex={-1}>
                      View
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

export default function QueuePage() {
  return (
    <div className={styles.container}>
      <div className={styles.pageHeader}>
        <h1 className={styles.pageTitle}>Exception Queue</h1>
        <p className={styles.pageSubtitle}>Prioritized by financial exposure</p>
      </div>
      <div className={styles.queueCard}>
        <Suspense fallback={<div className={styles.loading}>Loading queue...</div>}>
          <QueueContent />
        </Suspense>
      </div>
    </div>
  );
}
