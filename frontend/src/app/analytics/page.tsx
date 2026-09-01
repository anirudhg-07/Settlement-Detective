"use client";

import { useEffect, useState } from "react";
import styles from "./page.module.css";

interface MetricsData {
  run_id: string;
  records_processed: number;
  reconciled_bps: number;
  detection: { precision_bps: number; recall_bps: number; };
  classification: { scoreable: number; correct: number; incorrect: number; accuracy_bps: number; };
  investigation: { investigated: number; mean_evidence_score: number; mean_confidence_overclaim: number; };
}

interface ExceptionSummary {
  exception_id: string;
  exception_type: string | null;
  status: string;
  delta: { paise: number };
}

interface CauseAgg { name: string; count: number; moneyAtRisk: number; }

export default function AnalyticsPage() {
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [exceptions, setExceptions] = useState<ExceptionSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [mRes, eRes] = await Promise.all([
          fetch("/api/metrics"),
          fetch("/api/exceptions?limit=1000"),
        ]);
        if (mRes.ok) setMetrics(await mRes.json());
        if (eRes.ok) setExceptions((await eRes.json()).items || []);
      } catch {}
      finally { setLoading(false); }
    }
    load();
  }, []);

  const fmt = (paise: number) =>
    new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Math.abs(paise) / 100);

  if (loading) return (
    <div className={styles.loadingWrap}>
      {[1,2,3,4].map(i => <div key={i} className={styles.skeleton} style={{ height: 100 }} />)}
    </div>
  );

  if (!metrics) return (
    <div className={styles.emptyFull}>
      <div className={styles.emptyIcon}>—</div>
      <div className={styles.emptyTitle}>Metrics unavailable</div>
      <div className={styles.emptySub}>Run the evaluation pipeline first to see analytics.</div>
    </div>
  );

  const totalInv = metrics.investigation.investigated || 0;
  const isSmokeTest = totalInv < 50;

  // Aggregate exceptions
  const causesMap = new Map<string, CauseAgg>();
  let cntResolved = 0, cntEscalated = 0, cntRejected = 0;

  exceptions.forEach(ex => {
    if (ex.status === "RESOLVED") cntResolved++;
    if (ex.status === "ESCALATED") cntEscalated++;
    if (ex.status === "REJECTED") cntRejected++;
    const name = ex.exception_type?.replace(/_/g, " ") || "UNKNOWN";
    const amt = Math.abs(ex.delta.paise);
    if (!causesMap.has(name)) causesMap.set(name, { name, count: 0, moneyAtRisk: 0 });
    const c = causesMap.get(name)!;
    c.count++; c.moneyAtRisk += amt;
  });

  const causes = Array.from(causesMap.values()).sort((a, b) => b.count - a.count);
  const causesByMoney = [...causes].sort((a, b) => b.moneyAtRisk - a.moneyAtRisk);
  const maxCount = causes[0]?.count || 1;
  const maxMoney = causesByMoney[0]?.moneyAtRisk || 1;
  const maxOutcome = Math.max(cntResolved, cntEscalated, cntRejected, 1);

  const kpis = [
    {
      label: "Precision",
      value: `${(metrics.detection.precision_bps / 100).toFixed(2)}%`,
      desc: "Of exceptions flagged, how many were genuine exceptions.",
      color: "verified",
    },
    {
      label: "Recall",
      value: `${(metrics.detection.recall_bps / 100).toFixed(2)}%`,
      desc: "Of all true exceptions, how many were correctly detected.",
      color: "verified",
    },
    {
      label: "Classification Accuracy",
      value: `${(metrics.classification.accuracy_bps / 100).toFixed(2)}%`,
      desc: "How often the exception type was correctly categorized.",
      color: "verified",
    },
    {
      label: "Records Processed",
      value: metrics.records_processed.toLocaleString(),
      desc: "Total payment records in the settlement dataset.",
      color: "neutral",
    },
    {
      label: "Reconciliation Rate",
      value: `${(metrics.reconciled_bps / 100).toFixed(2)}%`,
      desc: "Share of payments reconciled (matched or in settlement window).",
      color: "neutral",
    },
    {
      label: "AI Investigations",
      value: totalInv.toString(),
      desc: `Cases investigated by Gemini.${isSmokeTest ? " Smoke test — not representative." : ""}`,
      color: isSmokeTest ? "attention" : "neutral",
    },
  ];

  return (
    <div className={styles.container}>
      <div className={styles.pageHeader}>
        <div>
          <h1 className={styles.pageTitle}>System Analytics</h1>
          <div className={styles.pageSubtitle}>{metrics.run_id}</div>
        </div>
      </div>

      {isSmokeTest && (
        <div className={styles.smokeTestBanner}>
          <strong>⚠ SMOKE TEST DATA</strong> — Only {totalInv} investigation{totalInv !== 1 ? "s" : ""} completed.
          These metrics are directional only and do not represent the full 10,055-record dataset.
        </div>
      )}

      {/* KPI Grid */}
      <div className={styles.kpiGrid}>
        {kpis.map(k => (
          <div key={k.label} className={`${styles.kpiCard} ${styles[`kpi_${k.color}`]}`}>
            <div className={styles.kpiLabel}>{k.label}</div>
            <div className={styles.kpiValue}>{k.value}</div>
            <div className={styles.kpiDesc}>{k.desc}</div>
          </div>
        ))}
      </div>

      {/* Charts */}
      <div className={styles.chartsRow}>
        {/* Exceptions by Cause */}
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Exceptions by Cause</div>
          <div className={styles.barList}>
            {causes.length === 0 ? (
              <div className={styles.chartEmpty}>No exceptions detected in this dataset.</div>
            ) : causes.map(c => (
              <div key={c.name} className={styles.barRow}>
                <div className={styles.barLabelLeft}>{c.name}</div>
                <div className={styles.barTrack}>
                  <div className={styles.barFill} style={{ width: `${(c.count / maxCount) * 100}%` }} />
                </div>
                <div className={styles.barVal}>{c.count}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Money at Risk */}
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Money at Risk by Cause</div>
          <div className={styles.barList}>
            {causesByMoney.length === 0 || maxMoney === 0 ? (
              <div className={styles.chartEmpty}>No financial exposure detected.</div>
            ) : causesByMoney.map(c => (
              <div key={c.name} className={styles.barRow}>
                <div className={styles.barLabelLeft}>{c.name}</div>
                <div className={styles.barTrack}>
                  <div className={`${styles.barFill} ${styles.barFillRisk}`} style={{ width: `${(c.moneyAtRisk / maxMoney) * 100}%` }} />
                </div>
                <div className={`${styles.barVal} ${styles.barValMoney}`}>{fmt(c.moneyAtRisk)}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className={styles.chartsRow}>
        {/* Investigation Outcomes */}
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Investigation Outcomes</div>
          {cntResolved + cntEscalated + cntRejected === 0 ? (
            <div className={styles.chartEmpty}>No actions taken yet. Investigate exceptions to record outcomes.</div>
          ) : (
            <div className={styles.barList}>
              <div className={styles.barRow}>
                <div className={styles.barLabelLeft}>Resolved</div>
                <div className={styles.barTrack}>
                  <div className={`${styles.barFill} ${styles.barFillVerified}`} style={{ width: `${(cntResolved / maxOutcome) * 100}%` }} />
                </div>
                <div className={styles.barVal}>{cntResolved}</div>
              </div>
              <div className={styles.barRow}>
                <div className={styles.barLabelLeft}>Escalated</div>
                <div className={styles.barTrack}>
                  <div className={`${styles.barFill} ${styles.barFillAttention}`} style={{ width: `${(cntEscalated / maxOutcome) * 100}%` }} />
                </div>
                <div className={styles.barVal}>{cntEscalated}</div>
              </div>
              <div className={styles.barRow}>
                <div className={styles.barLabelLeft}>Rejected</div>
                <div className={styles.barTrack}>
                  <div className={`${styles.barFill} ${styles.barFillNegative}`} style={{ width: `${(cntRejected / maxOutcome) * 100}%` }} />
                </div>
                <div className={styles.barVal}>{cntRejected}</div>
              </div>
            </div>
          )}
        </div>

        {/* Evidence Quality */}
        <div className={styles.chartCard}>
          <div className={styles.chartTitle}>Evidence vs Confidence</div>
          <div className={styles.scoreCompare}>
            <div className={styles.scoreBox}>
              <div className={styles.scoreBoxLabel}>MEAN EVIDENCE SCORE</div>
              <div className={`${styles.scoreBoxVal} ${metrics.investigation.mean_evidence_score < 50 ? styles.scoreBoxValLow : styles.scoreBoxValOk}`}>
                {metrics.investigation.mean_evidence_score.toFixed(1)}
              </div>
              <div className={styles.scoreBoxDesc}>Average verified evidence strength across investigated cases.</div>
            </div>
            <div className={styles.scoreBox}>
              <div className={styles.scoreBoxLabel}>MEAN CONFIDENCE OVERCLAIM</div>
              <div className={`${styles.scoreBoxVal} ${metrics.investigation.mean_confidence_overclaim > 0 ? styles.scoreBoxValAttention : styles.scoreBoxValOk}`}>
                {metrics.investigation.mean_confidence_overclaim.toFixed(1)} pts
              </div>
              <div className={styles.scoreBoxDesc}>Model claimed confidence above what evidence actually supported.</div>
            </div>
          </div>
          <div className={styles.scoreNote}>
            High model confidence does not guarantee sufficient evidence. These metrics measure the gap between Gemini's stated confidence and the system's deterministic evidence evaluation.
          </div>
        </div>
      </div>
    </div>
  );
}
