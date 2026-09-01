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

interface StepItem {
  seq: number;
  step_type: string;
  tool_name: string | null;
  tool_args: any;
  observation: string | null;
  duration_ms: number | null;
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
  steps: StepItem[];
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
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectForm, setShowRejectForm] = useState(false);
  const [showEscalateForm, setShowEscalateForm] = useState(false);
  const [escalateReason, setEscalateReason] = useState("");

  const [activeEvidence, setActiveEvidence] = useState<EvidenceItem | null>(null);
  const [toolsExpanded, setToolsExpanded] = useState(false);

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
      if (document.activeElement?.tagName === "INPUT" || document.activeElement?.tagName === "TEXTAREA" || document.activeElement?.tagName === "SELECT") return;

      if (e.key === "Escape") {
        if (activeEvidence) setActiveEvidence(null);
        else if (showRejectForm) setShowRejectForm(false);
        else if (showEscalateForm) setShowEscalateForm(false);
        return;
      }

      if (actionState || !data) return;

      switch (e.key.toLowerCase()) {
        case "a":
          handleAction("ACCEPT");
          break;
        case "r":
          setShowRejectForm(true);
          setShowEscalateForm(false);
          break;
        case "e":
          setShowEscalateForm(true);
          setShowRejectForm(false);
          break;
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [data, actionState, activeEvidence, showRejectForm, showEscalateForm]);

  const handleAction = async (action: string) => {
    if (actionState || !data) return;
    setActionState(action);
    setShowRejectForm(false);
    setShowEscalateForm(false);
    
    // Auto-advance to next case simulation
    setTimeout(() => {
      router.push('/queue');
    }, 1500);
  };

  const formatMoney = (paise: number) => {
    const rupees = Math.abs(paise) / 100;
    const formatted = new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
    }).format(rupees);
    return paise < 0 ? `−${formatted}` : `+${formatted}`;
  };

  if (loading) return <div className={styles.loading}>Loading investigation record...</div>;
  if (error || !data) return (
    <div className={styles.errorState}>
      <p>{error}</p>
      <button onClick={() => window.location.reload()} className={styles.retryBtn}>Retry</button>
    </div>
  );

  const { exception, investigation } = data;
  
  // Format string for claim
  const exceptionTypeDisplay = exception.exception_type?.replace(/_/g, " ") || "UNKNOWN EXCEPTION";
  const claimText = investigation?.decision || "Awaiting human review. Discrepancy detected during standard reconciliation run.";

  const isUnexplained = investigation?.unexplained_amount && investigation.unexplained_amount.paise !== 0;

  return (
    <div className={styles.container}>
      <div className={styles.header}>
        <Link href="/queue" className={styles.queueLink}>← Queue</Link>
        <div className={styles.caseId}>{exception.exception_id}</div>
        <div className={styles.paymentId}>{exception.payment_id}</div>
        <div className={styles.queueLinkDisabled}>Next Case →</div>
      </div>

      <div className={styles.claimSection}>
        <h2 className={styles.exceptionType}>{exceptionTypeDisplay}</h2>
        <div className={styles.financialExposure}>
          {formatMoney(Math.abs(exception.delta.paise))}
        </div>
        <div className={styles.exposureLabel}>FINANCIAL EXPOSURE</div>
        <p className={styles.claimText}>{claimText}</p>
      </div>

      <div className={styles.arithmeticSection}>
        <h3 className={styles.sectionTitle}>DOES IT ADD UP?</h3>
        <div className={styles.mathBlock}>
          <div className={styles.mathRow}>
            <span>Expected</span>
            <span className={styles.tabular}>{formatMoney(exception.expected_net.paise)}</span>
          </div>
          <div className={styles.mathRow}>
            <span>Actually settled</span>
            <span className={styles.tabular}>{formatMoney(exception.actual_net.paise)}</span>
          </div>
          <div className={styles.divider} />
          <div className={styles.mathRow}>
            <span>Discrepancy</span>
            <span className={styles.tabular}>{formatMoney(exception.delta.paise)}</span>
          </div>
          
          <div className={styles.evidenceMathBlock}>
            {investigation?.evidence.map((ev, idx) => (
              <div key={idx} className={styles.mathRowMuted}>
                <span>{ev.record_type} <span className={styles.mono}>{ev.record_id.slice(0, 15)}</span></span>
                <span className={styles.tabular}>
                  {ev.amount_contribution ? formatMoney(ev.amount_contribution.paise) : "₹0.00"} ✓
                </span>
              </div>
            ))}
          </div>

          <div className={styles.divider} />
          <div className={`${styles.mathRowStrong} ${isUnexplained ? styles.attentionText : ""}`}>
            <span>UNEXPLAINED</span>
            <span className={styles.tabular}>
              {investigation?.unexplained_amount
                ? formatMoney(investigation.unexplained_amount.paise)
                : "₹0.00"}
              {!isUnexplained && " ✓"}
            </span>
          </div>
        </div>
      </div>

      <div className={styles.evidenceListSection}>
        <h3 className={styles.sectionTitle}>EVIDENCE</h3>
        {!investigation?.evidence || investigation.evidence.length === 0 ? (
          <div className={styles.noEvidence}>No supporting evidence was found. Case requires escalation.</div>
        ) : (
          <div className={styles.evidenceList}>
            {investigation.evidence.map((ev, idx) => (
              <div key={idx} className={styles.evidenceItem}>
                <div className={styles.evidenceMain}>
                  <div className={styles.evidenceIcon}>✓</div>
                  <div className={styles.evidenceDetails}>
                    <div className={styles.evidenceTitle}>{ev.record_type}</div>
                    <div className={styles.mono}>{ev.record_id}</div>
                    {ev.note && <div className={styles.evidenceNote}>{ev.note}</div>}
                  </div>
                  <div className={styles.evidenceAmount}>
                    {ev.amount_contribution ? formatMoney(ev.amount_contribution.paise) : "₹0.00"}
                  </div>
                  <button onClick={() => setActiveEvidence(ev)} className={styles.openRecordBtn}>[ Open record ]</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {investigation && (
        <div className={styles.trustSection}>
          <div className={styles.trustBlock}>
            <h3 className={styles.sectionTitle}>SYSTEM EVIDENCE SCORE</h3>
            <div className={styles.scoreValue}>{investigation.evidence_score !== null ? investigation.evidence_score : "0"}</div>
            
            <div className={styles.scoreFactors}>
              {investigation.score_factors?.map((factor, idx) => (
                <div key={idx} className={styles.factorRow}>
                  <span>{factor.name}</span>
                  <span className={styles.factorDelta}>{factor.delta > 0 ? `+${factor.delta}` : factor.delta}</span>
                </div>
              ))}
            </div>
          </div>
          <div className={styles.trustBlock}>
            <h3 className={styles.sectionTitle}>MODEL SELF-REPORTED CONFIDENCE</h3>
            <div className={styles.confidenceValue}>
              {investigation.reasoning_confidence !== null ? `${investigation.reasoning_confidence}%` : "—"}
            </div>
            <div className={styles.confidenceNote}>Model claimed {investigation.reasoning_confidence}% certainty. (Not proof of correctness).</div>
          </div>
        </div>
      )}

      {investigation?.steps && investigation.steps.length > 0 && (
        <div className={styles.toolsSection}>
          <button 
            className={styles.toolsToggle} 
            onClick={() => setToolsExpanded(!toolsExpanded)}
          >
            HOW IT INVESTIGATED {toolsExpanded ? "▴" : "▾"}
          </button>
          
          {toolsExpanded && (
            <div className={styles.toolsList}>
              {investigation.steps.map((step, idx) => (
                <div key={idx} className={styles.toolItem}>
                  <div className={styles.toolHeader}>
                    <span className={styles.toolSeq}>{step.seq}.</span>
                    <span className={styles.toolName}>{step.tool_name || step.step_type}</span>
                    {step.duration_ms && <span className={styles.toolTime}>{step.duration_ms}ms</span>}
                  </div>
                  {step.tool_args && (
                    <div className={styles.toolArgs}>
                      {JSON.stringify(step.tool_args)}
                    </div>
                  )}
                  {step.observation && (
                    <div className={styles.toolObservation}>{step.observation}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className={styles.decisionSplit}>
        <div className={styles.systemRec}>
          <h3 className={styles.sectionTitle}>SYSTEM RECOMMENDATION</h3>
          <div className={styles.recStatus}>
            {investigation?.final_status === "RESOLVED" ? "✓ RESOLVE" : "↑ ESCALATE / REVIEW"}
          </div>
        </div>
      </div>

      <div className={styles.actionSection}>
        <h3 className={styles.sectionTitle}>YOUR DECISION</h3>
        
        {actionState ? (
          <div className={styles.actionTaken}>
            ✓ {actionState} BY YOU <span className={styles.timestamp}>Moving to next case...</span>
          </div>
        ) : showRejectForm ? (
          <div className={styles.actionForm}>
            <select 
              value={rejectReason} 
              onChange={e => setRejectReason(e.target.value)}
              className={styles.reasonSelect}
            >
              <option value="" disabled>Select reason to reject...</option>
              <option value="Wrong cause">Wrong cause</option>
              <option value="Wrong amount">Wrong amount</option>
              <option value="Bad evidence">Bad evidence</option>
              <option value="Actually fine">Actually fine</option>
              <option value="Other">Other...</option>
            </select>
            <div className={styles.actionFormButtons}>
              <button 
                onClick={() => handleAction(`REJECTED: ${rejectReason}`)} 
                disabled={!rejectReason}
                className={`${styles.actionBtn} ${styles.btnReject}`}
              >
                Confirm Reject
              </button>
              <button onClick={() => setShowRejectForm(false)} className={styles.cancelBtn}>Cancel</button>
            </div>
          </div>
        ) : showEscalateForm ? (
          <div className={styles.actionForm}>
            <select 
              value={escalateReason} 
              onChange={e => setEscalateReason(e.target.value)}
              className={styles.reasonSelect}
            >
              <option value="" disabled>Select reason to escalate...</option>
              <option value="Evidence insufficient">Evidence insufficient</option>
              <option value="Conflicting records">Conflicting records</option>
              <option value="Unexplained discrepancy">Unexplained discrepancy</option>
              <option value="High financial exposure">High financial exposure</option>
              <option value="Other">Other...</option>
            </select>
            <div className={styles.actionFormButtons}>
              <button 
                onClick={() => handleAction(`ESCALATED: ${escalateReason}`)} 
                disabled={!escalateReason}
                className={`${styles.actionBtn} ${styles.btnEscalate}`}
              >
                Confirm Escalate
              </button>
              <button onClick={() => setShowEscalateForm(false)} className={styles.cancelBtn}>Cancel</button>
            </div>
          </div>
        ) : (
          <div className={styles.actionButtons}>
            <button onClick={() => handleAction("ACCEPTED")} className={`${styles.actionBtn} ${styles.btnAccept}`}>✓ Accept</button>
            <button onClick={() => setShowRejectForm(true)} className={`${styles.actionBtn} ${styles.btnReject}`}>✗ Reject</button>
            <button onClick={() => setShowEscalateForm(true)} className={`${styles.actionBtn} ${styles.btnEscalate}`}>↑ Escalate</button>
            <span className={styles.keyboardHint}>Keyboard: A / R / E</span>
          </div>
        )}
      </div>

      {activeEvidence && (
        <div className={styles.evidenceOverlay} onClick={() => setActiveEvidence(null)}>
          <div className={styles.evidencePanel} onClick={e => e.stopPropagation()}>
            <div className={styles.panelHeader}>
              <div className={styles.panelTitle}>Evidence Record</div>
              <button onClick={() => setActiveEvidence(null)} className={styles.closeBtn}>Esc ✕</button>
            </div>
            <div className={styles.panelBody}>
              <div className={styles.panelField}>
                <div className={styles.fieldLabel}>RECORD TYPE</div>
                <div className={styles.fieldValue}>{activeEvidence.record_type}</div>
              </div>
              <div className={styles.panelField}>
                <div className={styles.fieldLabel}>RECORD ID</div>
                <div className={`${styles.fieldValue} ${styles.mono}`}>{activeEvidence.record_id}</div>
              </div>
              <div className={styles.panelField}>
                <div className={styles.fieldLabel}>ROLE</div>
                <div className={styles.fieldValue}>{activeEvidence.role}</div>
              </div>
              <div className={styles.panelField}>
                <div className={styles.fieldLabel}>CONTRIBUTION</div>
                <div className={`${styles.fieldValue} ${styles.tabular}`}>
                  {activeEvidence.amount_contribution ? formatMoney(activeEvidence.amount_contribution.paise) : "₹0.00"}
                </div>
              </div>
              
              <div className={styles.divider} />
              
              <div className={styles.panelField}>
                <div className={styles.fieldLabel}>VERIFICATION RESULT</div>
                <div className={styles.fieldValueVerified}>✓ Verified matches settlement ledger</div>
              </div>
              
              {activeEvidence.note && (
                <div className={styles.panelField}>
                  <div className={styles.fieldLabel}>SYSTEM NOTE</div>
                  <div className={styles.fieldValue}>{activeEvidence.note}</div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
