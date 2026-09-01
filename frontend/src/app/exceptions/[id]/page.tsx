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

  const getConclusionText = (type: string | null) => {
    if (!type) return "Awaiting investigation.";
    if (type.includes("MISSING_REFUND")) return "Refund was expected but never debited from payout.";
    if (type.includes("MISSING_SETTLEMENT")) return "Payment captured but settlement never arrived.";
    if (type.includes("FEE")) return "Charged fee differs from expected standard rate.";
    if (type.includes("DUPLICATE")) return "Multiple settlement lines exist for one capture.";
    return "Reconciliation engine flagged a discrepancy.";
  };

  if (loading) return <div className={styles.loading}>Loading investigation record...</div>;
  if (error || !data) return (
    <div className={styles.errorState}>
      <p>{error}</p>
      <button onClick={() => window.location.reload()} className={styles.retryBtn}>Retry</button>
    </div>
  );

  const { exception, investigation } = data;
  const exceptionTypeDisplay = exception.exception_type?.replace(/_/g, " ") || "UNKNOWN EXCEPTION";
  const isUnexplained = investigation?.unexplained_amount && investigation.unexplained_amount.paise !== 0;

  // Filter tool steps for the tool trail (hide pure LLM think steps unless it's the finding)
  const toolSteps = investigation?.steps.filter(s => 
    s.step_type === "tool_call" || s.step_type === "finding"
  ) || [];

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
        <p className={styles.claimText}>{getConclusionText(exception.exception_type)}</p>
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
        <h3 className={styles.sectionTitle}>EVIDENCE RECORDS</h3>
        {!investigation ? (
          <div className={styles.noEvidence}>Investigation pending. Records will be gathered by the agent.</div>
        ) : !investigation.evidence || investigation.evidence.length === 0 ? (
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

      {/* NEW AI INVESTIGATION SECTION */}
      <div className={styles.aiSection}>
        <div className={styles.aiHeader}>
          <h3 className={styles.aiTitle}>GEMINI AI INVESTIGATION</h3>
        </div>
        
        {investigation ? (
          <div className={styles.aiBody}>
            <div className={styles.aiBlock}>
              <div className={styles.aiLabel}>Reasoning</div>
              <div className={styles.aiReasoningText}>"{investigation.decision}"</div>
            </div>

            <div className={styles.aiBlock}>
              <div className={styles.aiLabel}>Evidence used</div>
              {investigation.evidence.length > 0 ? (
                <div className={styles.aiEvidenceList}>
                  {investigation.evidence.map((ev, idx) => (
                    <div key={idx} className={styles.aiEvidenceRow}>
                      <span className={styles.aiCheck}>✓</span> {ev.record_type} <span className={styles.monoMuted}>{ev.record_id}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className={styles.aiEvidenceRowMuted}>None found.</div>
              )}
            </div>

            <div className={styles.aiBlock}>
              <button 
                className={styles.toolsToggle} 
                onClick={() => setToolsExpanded(!toolsExpanded)}
              >
                TOOL TRAIL {toolsExpanded ? "▴" : "▾"}
              </button>
              
              {toolsExpanded && toolSteps.length > 0 && (
                <div className={styles.toolsList}>
                  {toolSteps.map((step, idx) => (
                    <div key={idx} className={styles.toolItem}>
                      <div className={styles.toolHeader}>
                        <span className={styles.toolSeq}>{idx + 1}.</span>
                        <span className={styles.toolName}>{step.tool_name || step.step_type}</span>
                        {step.duration_ms !== null && <span className={styles.toolTime}>{step.duration_ms}ms</span>}
                      </div>
                      {step.tool_args && (
                        <div className={styles.toolArgs}>
                          args: {JSON.stringify(step.tool_args)}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className={styles.aiScoresDivider} />
            
            <div className={styles.aiScoresSplit}>
              <div className={styles.aiScoreCol}>
                <div className={styles.aiLabel}>MODEL CONFIDENCE</div>
                <div className={styles.aiScoreValue}>{investigation.reasoning_confidence ?? "—"}%</div>
                <div className={styles.aiScoreSub}>Gemini's stated confidence</div>
              </div>
              
              <div className={styles.aiScoreCol}>
                <div className={styles.aiLabel}>EVIDENCE SCORE</div>
                <div className={styles.aiScoreValueStrong}>{investigation.evidence_score ?? "0"}</div>
                <div className={styles.aiScoreSub}>Verified evidence support</div>
              </div>
            </div>
            
            <div className={styles.aiWarning}>
              High model confidence does not override insufficient evidence.
            </div>
            
            <div className={styles.aiScoresDivider} />
            
            <div className={styles.aiDecisionBlock}>
              <div className={styles.aiLabel}>SYSTEM FINAL DECISION</div>
              <div className={`${styles.aiDecisionValue} ${
                investigation.final_status === "RESOLVED" ? styles.decisionResolved : styles.decisionEscalated
              }`}>
                {investigation.final_status} {investigation.final_status === "ESCALATED" && investigation.evidence_score === 0 ? "— insufficient verified evidence" : ""}
              </div>
              {investigation.score_factors && investigation.score_factors.length > 0 && (
                <div className={styles.rationaleBlock}>
                  <div className={styles.rationaleTitle}>Decision Rationale:</div>
                  <ul className={styles.rationaleList}>
                    {investigation.score_factors.map((sf, idx) => (
                      <li key={idx} className={styles.rationaleItem}>
                        <span className={styles.rationaleDetail}>{sf.detail}</span>
                        <span className={styles.rationaleBadge}>Evidence score {sf.delta > 0 ? `+${sf.delta}` : sf.delta}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className={styles.aiEmptyState}>
            <p className={styles.aiEmptyStateTitle}>Investigation not yet available</p>
            <p className={styles.aiEmptyStateDesc}>This exception has been detected but has not been investigated by the agent yet.</p>
          </div>
        )}
      </div>

      <div className={styles.actionSection}>
        <h3 className={styles.sectionTitle}>YOUR ACTION</h3>
        
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
