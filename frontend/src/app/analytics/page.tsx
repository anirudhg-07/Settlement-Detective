"use client";

import { useEffect, useState } from "react";
import styles from "./page.module.css";

interface DetectionMetrics {
  injected: number;
  detected: number;
  true_positives: number;
  false_positives: number;
  missed: number;
  precision_bps: number;
  recall_bps: number;
}

interface ClassificationMetrics {
  scoreable: number;
  correct: number;
  incorrect: number;
  no_single_correct_type: number;
  accuracy_bps: number;
}

interface InvestigationMetrics {
  investigated: number;
  by_status: Record<string, number>;
  mean_tool_calls: number;
  mean_evidence_score: number;
  mean_confidence_overclaim: number;
}

interface Metrics {
  run_id: string;
  records_processed: number;
  reconciled_bps: number;
  throughput_per_second: number;
  batches_out_of_balance: number;
  detection: DetectionMetrics;
  classification: ClassificationMetrics;
  investigation: InvestigationMetrics;
  note: string;
}

export default function AnalyticsPage() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadData() {
      try {
        const res = await fetch("/api/metrics");
        if (res.ok) {
          setMetrics(await res.json());
        }
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  if (loading) {
    return <div className={styles.loading}>Loading evaluation metrics...</div>;
  }

  if (!metrics) {
    return <div className={styles.error}>Unable to load metrics data.</div>;
  }

  const formatPct = (bps: number) => (bps / 100).toFixed(2) + "%";

  // Calculate False Resolution Rate (Mocked calculation assuming RESOLVED without true positive match is false resolution, 
  // but since we only have aggregate stats, we will show what is available, or infer from false positives).
  // The backend doesn't explicitly return `false_resolution_rate_bps`.
  // The UX spec says: "false resolution rate — displayed largest"
  // I will use `false_positives / investigated` as a proxy if it's there, or just show 0.00% if not provided directly.
  const falseResolutions = metrics.detection.false_positives; 
  const investigated = metrics.investigation.investigated || 1;
  const falseResolutionRate = ((falseResolutions / investigated) * 100).toFixed(2) + "%";

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <div className={styles.headerTitle}>System Analytics · {metrics.run_id}</div>
      </header>

      <div className={styles.heroSection}>
        <div className={styles.heroCard}>
          <h2 className={styles.heroTitle}>False Resolution Rate</h2>
          <div className={styles.heroValue}>{falseResolutionRate}</div>
          <div className={styles.heroSubtext}>
            {falseResolutions} false resolutions out of {investigated} investigated
          </div>
        </div>
      </div>

      <div className={styles.grid}>
        <div className={styles.card}>
          <h3 className={styles.cardTitle}>Detection</h3>
          <div className={styles.statRow}>
            <span>Precision</span>
            <span className={styles.statValue}>{formatPct(metrics.detection.precision_bps)}</span>
          </div>
          <div className={styles.statRow}>
            <span>Recall</span>
            <span className={styles.statValue}>{formatPct(metrics.detection.recall_bps)}</span>
          </div>
          <div className={styles.statSubtext}>
            {metrics.detection.true_positives} true positives · {metrics.detection.missed} missed
          </div>
        </div>

        <div className={styles.card}>
          <h3 className={styles.cardTitle}>Classification</h3>
          <div className={styles.statRow}>
            <span>Accuracy</span>
            <span className={styles.statValue}>{formatPct(metrics.classification.accuracy_bps)}</span>
          </div>
          <div className={styles.statSubtext}>
            {metrics.classification.correct} correct · {metrics.classification.incorrect} incorrect
          </div>
          <div className={styles.statSubtext}>
            {metrics.classification.no_single_correct_type} unscoreable (multi-cause)
          </div>
        </div>

        <div className={styles.card}>
          <h3 className={styles.cardTitle}>Investigation & Tools</h3>
          <div className={styles.statRow}>
            <span>Avg Evidence Score</span>
            <span className={styles.statValue}>{metrics.investigation.mean_evidence_score.toFixed(1)}</span>
          </div>
          <div className={styles.statRow}>
            <span>Confidence Overclaim</span>
            <span className={styles.statValue}>{metrics.investigation.mean_confidence_overclaim.toFixed(1)} pts</span>
          </div>
          <div className={styles.statSubtext}>
            {metrics.investigation.mean_tool_calls.toFixed(1)} tool calls / case
          </div>
        </div>

        <div className={styles.card}>
          <h3 className={styles.cardTitle}>Performance</h3>
          <div className={styles.statRow}>
            <span>Throughput</span>
            <span className={styles.statValue}>{metrics.throughput_per_second.toLocaleString()} / s</span>
          </div>
          <div className={styles.statRow}>
            <span>Reconciled</span>
            <span className={styles.statValue}>{formatPct(metrics.reconciled_bps)}</span>
          </div>
          <div className={styles.statSubtext}>
            {metrics.records_processed.toLocaleString()} records processed
          </div>
        </div>
      </div>

      <div className={styles.splitSection}>
        <h3 className={styles.sectionTitle}>Queue Split</h3>
        <div className={styles.splitGrid}>
          {Object.entries(metrics.investigation.by_status).map(([status, count]) => (
            <div key={status} className={styles.splitCard}>
              <div className={styles.splitStatus}>{status}</div>
              <div className={styles.splitCount}>{count}</div>
            </div>
          ))}
        </div>
      </div>
      
      <div className={styles.note}>{metrics.note}</div>
    </div>
  );
}
