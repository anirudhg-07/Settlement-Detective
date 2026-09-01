"use client";

import { useEffect, useState } from "react";
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
  const filter = searchParams.get("filter") || "needs_you"; // needs_you, auto_resolved, all

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

  const getStatusLabel = (status: string) => {
    switch (status) {
      case "RESOLVED": return "RESOLVED ✓";
      case "ESCALATED": return "ESCALATED ↑";
      case "REJECTED": return "REJECTED ✕";
      default: return status;
    }
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
          <div className={styles.loading}>Loading cases...</div>
        ) : !page || page.items.length === 0 ? (
          <div className={styles.empty}>
            {filter === "needs_you"
              ? "No cases are waiting for your review."
              : "No cases match these filters."}
          </div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>CASE</th>
                <th>CAUSE</th>
                <th className={styles.rightAlign}>AT RISK</th>
                <th className={styles.rightAlign}>CONF</th>
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
                  <td>{item.exception_type?.replace(/_/g, " ") || "UNKNOWN"}</td>
                  <td className={`${styles.rightAlign} ${styles.tabular} ${
                    item.delta.paise < 0 ? styles.negative : styles.attention
                  }`}>
                    {item.delta.paise === 0 ? "₹0.00" : formatMoney(item.delta.paise)}
                  </td>
                  <td className={`${styles.rightAlign} ${styles.tabular}`}>
                    {item.evidence_score !== null ? item.evidence_score : "—"}
                  </td>
                  <td className={styles.statusCell}>
                    {getStatusLabel(item.status)}
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

import { Suspense } from "react";

export default function QueuePage() {
  return (
    <div className={styles.container}>
      <Suspense fallback={<div className={styles.loading}>Loading queue...</div>}>
        <QueueContent />
      </Suspense>
    </div>
  );
}
