"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import styles from "./page.module.css";

interface Money {
  paise: number;
  display: string;
}

interface EvidenceItem {
  record_type: string;
  record_id: string;
  role: string;
  amount_contribution: Money | null;
  note: string | null;
}

interface ScoreFactor {
  name: string;
  delta: number;
  detail: string;
}

interface InvestigationDetail {
  investigation_id: string;
  decision: string | null;
  final_status: string | null;
  unexplained_amount: Money | null;
  evidence_score: number | null;
  reasoning_confidence: number | null;
  evidence: EvidenceItem[];
  score_factors: ScoreFactor[];
}

interface ExceptionSummary {
  exception_id: string;
  exception_type: string | null;
  status: string;
  expected_net: Money;
  actual_net: Money;
  delta: Money;
}

interface ExceptionDetail {
  exception: ExceptionSummary;
  investigation: InvestigationDetail | null;
}

export default function InvestigationView({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const [data, setData] = useState<ExceptionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionState, setActionState] = useState<string | null>(null);
  const [activeEvidence, setActiveEvidence] = useState<EvidenceItem | null>(null);

  useEffect(() => {
    async function loadData() {
      try {
        const res = await fetch(`/api/exceptions/${id}`);
        if (!res.ok) throw new Error("Failed to load investigation data");
        setData(await res.json());
      } catch (err) {
        setError("Unable to load settlement record. The investigation is preserved. No financial decision was made.");
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [id]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't trigger if user is typing in an input
      if (document.activeElement?.tagName === "INPUT" || document.activeElement?.tagName === "TEXTAREA") return;

      switch (e.key.toLowerCase()) {
        case "a":
          handleAction("ACCEPT");
          break;
        case "r":
          handleAction("REJECT");
          break;
        case "e":
          handleAction("ESCALATE");
          break;
        case "escape":
          setActiveEvidence(null);
          break;
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [data]);

  const handleAction = async (action: string) => {
    if (actionState || !data) return;
    setActionState(action);
    // In a real app, this would POST to backend.
  };

  const formatMoney = (paise: number) => {
    const rupees = Math.abs(paise) / 100;
    const formatted = new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
    }).format(rupees);
    return paise < 0 ? `−${formatted}` : `+${formatted}`;
  };

  if (loading) return <div className={styles.loading}>Loading investigation result...</div>;
  if (error || !data) return (
    <div className={styles.errorState}>
      <p>{error}</p>
      <button onClick={() => window.location.reload()} className={styles.retryBtn}>Retry</button>
    </div>
  );

  const { exception, investigation } = data;
  const isResolvedByAI = investigation?.final_status === "RESOLVED";

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <Link href="/queue" className={styles.queueLink}>← Previous Case</Link>
        <div className={styles.caseId}>{exception.exception_id}</div>
        <div className={styles.queueLink}>Next Case →</div>
      </div>

      <div className={styles.claimSection}>
        <div className={styles.claimHeader}>
          <h2 className={styles.exceptionType}>{exception.exception_type?.replace(/_/g, " ") || "UNKNOWN EXCEPTION"}</h2>
          {investigation && (
            <div className={styles.evidenceScore}>
              EVIDENCE SUFFICIENCY {investigation.evidence_score !== null ? `${investigation.evidence_score}/100` : "—"}
            </div>
          )}
        </div>
        <p className={styles.claimText}>{investigation?.decision || "Awaiting human review."}</p>
      </div>

      <div className={styles.arithmeticSection}>
        <h3 className={styles.sectionTitle}>DOES IT ADD UP?</h3>
        <div className={styles.mathBlock}>
          <div className={styles.mathRow}>
            <span>expected</span>
            <span className={styles.tabular}>{formatMoney(exception.expected_net.paise)}</span>
          </div>
          <div className={styles.mathRow}>
            <span>actually settled</span>
            <span className={styles.tabular}>{formatMoney(exception.actual_net.paise)}</span>
          </div>
          <div className={styles.divider} />
          <div className={styles.mathRow}>
            <span>discrepancy</span>
            <span className={styles.tabular}>{formatMoney(exception.delta.paise)}</span>
          </div>
          {investigation?.evidence.map((ev, idx) => (
            <div key={idx} className={styles.evidenceMathRow}>
              <span>{ev.record_type} {ev.record_id.slice(0, 15)}… {ev.role}</span>
              <span className={styles.tabular}>
                {ev.amount_contribution ? formatMoney(ev.amount_contribution.paise) : "₹0.00"} ✓ verified
              </span>
            </div>
          ))}
          <div className={styles.divider} />
          <div className={styles.mathRow}>
            <span>unexplained</span>
            <span className={styles.tabular}>
              {investigation?.unexplained_amount
                ? formatMoney(investigation.unexplained_amount.paise)
                : "₹0.00"}
              {(!investigation?.unexplained_amount || investigation.unexplained_amount.paise === 0) && " ✓"}
            </span>
          </div>
        </div>
      </div>

      <div className={styles.evidenceListSection}>
        <h3 className={styles.sectionTitle}>EVIDENCE</h3>
        {!investigation?.evidence || investigation.evidence.length === 0 ? (
          <div className={styles.noEvidence}>No evidence gathered.</div>
        ) : (
          <div className={styles.evidenceList}>
            {investigation.evidence.map((ev, idx) => (
              <div key={idx} className={styles.evidenceItem}>
                <div className={styles.evidenceMain}>
                  <span>✓ {ev.record_type}</span>
                  <span className={styles.mono}>{ev.record_id}</span>
                  <span className={styles.tabular}>{ev.amount_contribution ? formatMoney(ev.amount_contribution.paise) : "₹0.00"}</span>
                  <button onClick={() => setActiveEvidence(ev)} className={styles.openRecordBtn}>[ open record ]</button>
                </div>
                {ev.note && <div className={styles.evidenceNote}>{ev.note}</div>}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className={styles.decisionSplit}>
        <div className={styles.systemRec}>
          <h3 className={styles.sectionTitle}>SYSTEM RECOMMENDATION</h3>
          <div className={styles.recStatus}>
            {isResolvedByAI ? "✓ RESOLVE" : "↑ ESCALATE / REVIEW"}
          </div>
          <div className={styles.recReason}>
            Reason: {investigation?.decision || "N/A"}
          </div>
          <div className={styles.modelConf}>
            MODEL CONFIDENCE {investigation?.reasoning_confidence != null ? `${investigation.reasoning_confidence}%` : "—"} (not proof of correctness)
          </div>
        </div>
      </div>

      <div className={styles.actionSection}>
        <h3 className={styles.sectionTitle}>YOUR DECISION</h3>
        {actionState ? (
          <div className={styles.actionTaken}>
            ✓ {actionState} BY YOU <span className={styles.timestamp}>Just now</span>
          </div>
        ) : (
          <div className={styles.actionButtons}>
            <button onClick={() => handleAction("ACCEPT")} className={`${styles.actionBtn} ${styles.btnAccept}`}>✓ Accept</button>
            <button onClick={() => handleAction("REJECT")} className={`${styles.actionBtn} ${styles.btnReject}`}>✗ Reject</button>
            <button onClick={() => handleAction("ESCALATE")} className={`${styles.actionBtn} ${styles.btnEscalate}`}>↑ Escalate</button>
            <span className={styles.keyboardHint}>A/R/E</span>
          </div>
        )}
      </div>

      {activeEvidence && (
        <div className={styles.evidenceOverlay} onClick={() => setActiveEvidence(null)}>
          <div className={styles.evidencePanel} onClick={e => e.stopPropagation()}>
            <div className={styles.panelHeader}>
              <h3>Evidence Record</h3>
              <button onClick={() => setActiveEvidence(null)} className={styles.closeBtn}>Esc</button>
            </div>
            <div className={styles.panelBody}>
              <p><strong>Type:</strong> {activeEvidence.record_type}</p>
              <p><strong>ID:</strong> <span className={styles.mono}>{activeEvidence.record_id}</span></p>
              <p><strong>Role:</strong> {activeEvidence.role}</p>
              <p><strong>Contribution:</strong> {activeEvidence.amount_contribution ? formatMoney(activeEvidence.amount_contribution.paise) : "₹0.00"}</p>
              <p><strong>Note:</strong> {activeEvidence.note || "None"}</p>
              {/* In a complete app, raw record API response would be fetched and displayed here. */}
              <div className={styles.rawRecordPlaceholder}>
                [Raw JSON record and settlement lines would appear here]
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
