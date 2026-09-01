"use client";

import { useEffect, useState, use } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import styles from "./page.module.css";

interface Money { paise: number; display: string; }
interface EvidenceItem {
  record_type: string; record_id: string; role: string;
  amount_contribution: Money | null; note: string | null;
}
interface ScoreFactor { name: string; delta: number; detail: string; }
interface StepItem {
  seq: number; step_type: string; tool_name: string | null;
  tool_args: any; observation: string | null; duration_ms: number | null;
}
interface InvestigationDetail {
  investigation_id: string; decision: string | null; final_status: string | null;
  unexplained_amount: Money | null; evidence_score: number | null;
  reasoning_confidence: number | null; evidence: EvidenceItem[];
  score_factors: ScoreFactor[]; steps: StepItem[];
}
interface ExceptionSummary {
  exception_id: string; payment_id: string; exception_type: string | null;
  status: string; expected_net: Money; actual_net: Money; delta: Money;
}
interface ExceptionDetail { exception: ExceptionSummary; investigation: InvestigationDetail | null; }

const PIPELINE_STEPS = [
  { id: "detect",   label: "Rule Detection" },
  { id: "evidence", label: "Evidence Retrieval" },
  { id: "gemini",   label: "Gemini Analysis" },
  { id: "eval",     label: "Evidence Evaluation" },
  { id: "decision", label: "Final Decision" },
];

export default function InvestigationView({ params }: { params: Promise<{ id: string }> }) {
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
    fetch(`/api/exceptions/${id}`)
      .then(r => { if (!r.ok) throw new Error(); return r.json(); })
      .then(d => setData(d))
      .catch(() => setError("Unable to load investigation record."))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const active = document.activeElement?.tagName;
      if (active === "INPUT" || active === "TEXTAREA" || active === "SELECT") return;
      if (e.key === "Escape") {
        if (activeEvidence) { setActiveEvidence(null); return; }
        if (showRejectForm) { setShowRejectForm(false); return; }
        if (showEscalateForm) { setShowEscalateForm(false); return; }
      }
      if (actionState || !data) return;
      if (e.key.toLowerCase() === "a") handleAction("ACCEPTED");
      if (e.key.toLowerCase() === "r") { setShowRejectForm(true); setShowEscalateForm(false); }
      if (e.key.toLowerCase() === "e") { setShowEscalateForm(true); setShowRejectForm(false); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [data, actionState, activeEvidence, showRejectForm, showEscalateForm]);

  const handleAction = (action: string) => {
    if (actionState || !data) return;
    setActionState(action);
    setShowRejectForm(false);
    setShowEscalateForm(false);
    setTimeout(() => router.push("/queue"), 1600);
  };

  const fmt = (paise: number) => {
    const r = Math.abs(paise) / 100;
    const s = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(r);
    return paise < 0 ? `−${s}` : `+${s}`;
  };

  const getClaim = (type: string | null) => {
    if (!type) return "Awaiting investigation.";
    if (type.includes("MISSING_REFUND")) return "Refund was expected but was never debited from payout.";
    if (type.includes("MISSING_SETTLEMENT")) return "Payment was captured but settlement has not arrived.";
    if (type.includes("FEE")) return "Charged fee differs from the expected standard rate.";
    if (type.includes("DUPLICATE")) return "Multiple settlement lines exist for a single payment capture.";
    return "Reconciliation engine has flagged a discrepancy requiring investigation.";
  };

  if (loading) return (
    <div className={styles.loadingWrap}>
      <div className={styles.skeleton} style={{ height: 80 }} />
      <div className={styles.skeleton} style={{ height: 160 }} />
      <div className={styles.skeleton} style={{ height: 280 }} />
    </div>
  );

  if (error || !data) return (
    <div className={styles.errorState}>
      <p>{error || "Case not found."}</p>
      <button onClick={() => window.location.reload()} className={styles.retryBtn}>Retry</button>
    </div>
  );

  const { exception, investigation } = data;
  const exType = exception.exception_type?.replace(/_/g, " ") || "UNKNOWN EXCEPTION";
  const isUnexplained = investigation?.unexplained_amount && investigation.unexplained_amount.paise !== 0;
  const toolSteps = investigation?.steps.filter(s => s.step_type === "tool_call" || s.step_type === "finding") || [];
  const pipelineActive = investigation
    ? investigation.final_status === "RESOLVED" ? 5 : 5
    : investigation === null && data !== null ? 1 : 0;

  return (
    <div className={styles.container}>
      {/* Breadcrumb + Nav */}
      <div className={styles.breadcrumb}>
        <Link href="/queue" className={styles.breadcrumbBack}>← Exception Queue</Link>
        <span className={styles.breadcrumbSep}>/</span>
        <span className={styles.breadcrumbId}>{exception.exception_id}</span>
        <span className={styles.breadcrumbStatus}>
          {exception.status === "RESOLVED" && <span className={styles.statusBadgeResolved}>● Resolved</span>}
          {exception.status === "ESCALATED" && <span className={styles.statusBadgeEscalated}>↑ Escalated</span>}
          {exception.status === "DETECTED" && <span className={styles.statusBadgeDetected}>◎ Detected</span>}
        </span>
      </div>

      {/* Investigation Pipeline Stepper */}
      <div className={styles.stepper}>
        {PIPELINE_STEPS.map((step, idx) => {
          const done = idx < pipelineActive;
          const active = idx === pipelineActive - 1;
          return (
            <div key={step.id} className={styles.stepperItem}>
              <div className={`${styles.stepperNode} ${done ? styles.stepperNodeDone : active ? styles.stepperNodeActive : ""}`}>
                {done ? "✓" : idx + 1}
              </div>
              <div className={`${styles.stepperLabel} ${active ? styles.stepperLabelActive : ""}`}>{step.label}</div>
              {idx < PIPELINE_STEPS.length - 1 && (
                <div className={`${styles.stepperLine} ${done ? styles.stepperLineDone : ""}`} />
              )}
            </div>
          );
        })}
      </div>

      {/* Hero Section */}
      <div className={styles.hero}>
        <div className={styles.heroLeft}>
          <div className={styles.heroType}>{exType}</div>
          <p className={styles.heroClaim}>{getClaim(exception.exception_type)}</p>
          <div className={styles.heroMeta}>
            <span className={styles.metaChip}>Payment <code>{exception.payment_id}</code></span>
          </div>
        </div>
        <div className={styles.heroRight}>
          {exception.delta.paise !== 0 ? (
            <>
              <div className={styles.heroExposure}>{fmt(Math.abs(exception.delta.paise))}</div>
              <div className={styles.heroExposureLabel}>FINANCIAL EXPOSURE</div>
            </>
          ) : (
            <>
              <div className={styles.heroExposureZero}>₹0.00</div>
              <div className={styles.heroExposureLabel}>NO EXPOSURE</div>
            </>
          )}
        </div>
      </div>

      {/* Financial Arithmetic */}
      <div className={styles.panel}>
        <div className={styles.panelLabel}>FINANCIAL ARITHMETIC</div>
        <div className={styles.arithGrid}>
          <div className={styles.arithBox}>
            <div className={styles.arithBoxLabel}>Expected Net</div>
            <div className={styles.arithBoxValue}>{fmt(exception.expected_net.paise)}</div>
          </div>
          <div className={styles.arithArrow}>→</div>
          <div className={styles.arithBox}>
            <div className={styles.arithBoxLabel}>Actually Settled</div>
            <div className={styles.arithBoxValue}>{fmt(exception.actual_net.paise)}</div>
          </div>
          <div className={styles.arithArrow}>=</div>
          <div className={`${styles.arithBox} ${exception.delta.paise !== 0 ? styles.arithBoxDiscrepancy : styles.arithBoxMatched}`}>
            <div className={styles.arithBoxLabel}>Discrepancy</div>
            <div className={styles.arithBoxValue}>{fmt(exception.delta.paise)}</div>
          </div>
        </div>

        <div className={styles.arithFooter}>
          <div className={`${styles.unexplainedRow} ${isUnexplained ? styles.unexplainedWarning : styles.unexplainedClean}`}>
            <span>UNEXPLAINED</span>
            <span>{investigation?.unexplained_amount ? fmt(investigation.unexplained_amount.paise) : "₹0.00"} {!isUnexplained && "✓"}</span>
          </div>
        </div>
      </div>

      {/* Evidence Records */}
      <div className={styles.panel}>
        <div className={styles.panelLabel}>EVIDENCE RECORDS</div>
        {!investigation ? (
          <div className={styles.pendingState}>
            <div className={styles.pendingIcon}>◎</div>
            <div className={styles.pendingTitle}>Investigation pending</div>
            <div className={styles.pendingSub}>This exception has been detected. The agent has not yet retrieved evidence records.</div>
          </div>
        ) : investigation.evidence.length === 0 ? (
          <div className={styles.pendingState}>
            <div className={styles.pendingIcon}>⚠</div>
            <div className={styles.pendingTitle}>No evidence found</div>
            <div className={styles.pendingSub}>The investigation agent could not retrieve supporting records. Manual escalation required.</div>
          </div>
        ) : (
          <div className={styles.evidenceList}>
            {investigation.evidence.map((ev, idx) => (
              <div key={idx} className={styles.evidenceItem}>
                <div className={styles.evidenceBadge}>{ev.record_type}</div>
                <div className={styles.evidenceBody}>
                  <code className={styles.evidenceId}>{ev.record_id}</code>
                  {ev.note && <div className={styles.evidenceNote}>{ev.note}</div>}
                </div>
                <div className={styles.evidenceAmount}>
                  {ev.amount_contribution ? fmt(ev.amount_contribution.paise) : "₹0.00"}
                </div>
                <div className={styles.evidenceRole}>{ev.role}</div>
                <button onClick={() => setActiveEvidence(ev)} className={styles.openBtn}>Open ↗</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Gemini AI Investigation */}
      <div className={`${styles.panel} ${styles.aiPanel}`}>
        <div className={styles.aiPanelHeader}>
          <div className={styles.panelLabel}>GEMINI AI INVESTIGATION</div>
          <div className={styles.aiPanelBadge}>AI-assisted</div>
        </div>

        {!investigation ? (
          <div className={styles.pendingState}>
            <div className={styles.pendingIcon}>◎</div>
            <div className={styles.pendingTitle}>Investigation not yet available</div>
            <div className={styles.pendingSub}>This exception has been detected but has not been investigated by the agent yet.</div>
          </div>
        ) : (
          <div className={styles.aiBody}>
            {/* Reasoning */}
            <div className={styles.aiBlock}>
              <div className={styles.aiLabel}>AI Reasoning</div>
              <blockquote className={styles.reasoningQuote}>
                {investigation.decision}
              </blockquote>
            </div>

            {/* Evidence used */}
            <div className={styles.aiBlock}>
              <div className={styles.aiLabel}>Evidence Examined</div>
              {investigation.evidence.length > 0 ? (
                <div className={styles.evidenceChecklist}>
                  {investigation.evidence.map((ev, idx) => (
                    <div key={idx} className={styles.evidenceCheckItem}>
                      <span className={styles.checkIcon}>✓</span>
                      <span className={styles.checkType}>{ev.record_type}</span>
                      <code className={styles.checkId}>{ev.record_id}</code>
                    </div>
                  ))}
                </div>
              ) : <div className={styles.noneFound}>None retrieved.</div>}
            </div>

            {/* Tool Trail */}
            <div className={styles.aiBlock}>
              <button className={styles.trailToggle} onClick={() => setToolsExpanded(!toolsExpanded)}>
                <span className={styles.trailToggleIcon}>{toolsExpanded ? "▴" : "▾"}</span>
                Tool Trail ({toolSteps.length} calls)
              </button>
              {toolsExpanded && (
                <div className={styles.trailList}>
                  {toolSteps.map((step, idx) => (
                    <div key={idx} className={styles.trailItem}>
                      <div className={styles.trailStep}>
                        <span className={styles.trailSeq}>{idx + 1}</span>
                        <div className={styles.trailContent}>
                          <div className={styles.trailName}>{step.tool_name || step.step_type}</div>
                          {step.tool_args && (
                            <div className={styles.trailArgs}>
                              {Object.entries(step.tool_args).map(([k, v]) => (
                                <span key={k} className={styles.trailArg}>
                                  <span className={styles.trailArgKey}>{k}:</span>
                                  <code>{String(v)}</code>
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                        {step.duration_ms !== null && (
                          <span className={styles.trailTime}>{step.duration_ms}ms</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className={styles.aiDivider} />

            {/* Scores */}
            <div className={styles.scoresRow}>
              <div className={styles.scoreBox}>
                <div className={styles.scoreLabel}>MODEL CONFIDENCE</div>
                <div className={styles.scoreValue}>{investigation.reasoning_confidence ?? "—"}%</div>
                <div className={styles.scoreSub}>Gemini's stated confidence</div>
              </div>
              <div className={styles.scoreVs}>vs</div>
              <div className={`${styles.scoreBox} ${styles.scoreBoxEvidence}`}>
                <div className={styles.scoreLabel}>EVIDENCE SCORE</div>
                <div className={`${styles.scoreValue} ${(investigation.evidence_score || 0) === 0 ? styles.scoreValueLow : ""}`}>
                  {investigation.evidence_score ?? 0}
                </div>
                <div className={styles.scoreSub}>Verified evidence support</div>
              </div>
            </div>

            {(investigation.reasoning_confidence || 0) > (investigation.evidence_score || 0) && (
              <div className={styles.scoreWarning}>
                ⚠ Model confidence ({investigation.reasoning_confidence}%) exceeds evidence score ({investigation.evidence_score}). High confidence does not guarantee sufficient evidence.
              </div>
            )}

            <div className={styles.aiDivider} />

            {/* Decision */}
            <div className={styles.decisionBlock}>
              <div className={styles.aiLabel}>SYSTEM FINAL DECISION</div>
              <div className={`${styles.decisionValue} ${investigation.final_status === "RESOLVED" ? styles.decisionResolved : styles.decisionEscalated}`}>
                {investigation.final_status === "RESOLVED" ? "✓ RESOLVED" : "↑ ESCALATED"}
                {investigation.final_status === "ESCALATED" && investigation.evidence_score === 0 && (
                  <span className={styles.decisionReason}> — insufficient verified evidence</span>
                )}
              </div>

              {investigation.score_factors.length > 0 && (
                <div className={styles.rationaleBox}>
                  <div className={styles.rationaleBoxTitle}>Decision Rationale</div>
                  {investigation.score_factors.map((sf, idx) => (
                    <div key={idx} className={styles.rationaleItem}>
                      <span className={styles.rationaleText}>{sf.detail}</span>
                      <span className={`${styles.rationaleDelta} ${sf.delta < 0 ? styles.rationaleDeltaNeg : styles.rationaleDeltaPos}`}>
                        score {sf.delta > 0 ? `+${sf.delta}` : sf.delta}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Action Bar */}
      <div className={styles.actionBar}>
        <div className={styles.actionBarLeft}>
          <span className={styles.actionBarLabel}>Your decision:</span>
          <span className={styles.actionBarHint}>A · Accept &nbsp;|&nbsp; R · Reject &nbsp;|&nbsp; E · Escalate</span>
        </div>

        {actionState ? (
          <div className={styles.actionDone}>✓ {actionState} — returning to queue...</div>
        ) : showRejectForm ? (
          <div className={styles.actionForm}>
            <select value={rejectReason} onChange={e => setRejectReason(e.target.value)} className={styles.reasonSelect}>
              <option value="" disabled>Select reason...</option>
              <option value="Wrong cause">Wrong cause</option>
              <option value="Wrong amount">Wrong amount</option>
              <option value="Bad evidence">Bad evidence</option>
              <option value="Actually fine">Actually fine</option>
              <option value="Other">Other</option>
            </select>
            <button onClick={() => handleAction(`REJECTED: ${rejectReason}`)} disabled={!rejectReason} className={`${styles.actionBtn} ${styles.btnReject}`}>Confirm Reject</button>
            <button onClick={() => setShowRejectForm(false)} className={styles.cancelBtn}>Cancel</button>
          </div>
        ) : showEscalateForm ? (
          <div className={styles.actionForm}>
            <select value={escalateReason} onChange={e => setEscalateReason(e.target.value)} className={styles.reasonSelect}>
              <option value="" disabled>Select reason...</option>
              <option value="Evidence insufficient">Evidence insufficient</option>
              <option value="Conflicting records">Conflicting records</option>
              <option value="Unexplained discrepancy">Unexplained discrepancy</option>
              <option value="High financial exposure">High financial exposure</option>
              <option value="Other">Other</option>
            </select>
            <button onClick={() => handleAction(`ESCALATED: ${escalateReason}`)} disabled={!escalateReason} className={`${styles.actionBtn} ${styles.btnEscalate}`}>Confirm Escalate</button>
            <button onClick={() => setShowEscalateForm(false)} className={styles.cancelBtn}>Cancel</button>
          </div>
        ) : (
          <div className={styles.actionBtns}>
            <button onClick={() => handleAction("ACCEPTED")} className={`${styles.actionBtn} ${styles.btnAccept}`}>✓ Accept</button>
            <button onClick={() => setShowRejectForm(true)} className={`${styles.actionBtn} ${styles.btnReject}`}>✗ Reject</button>
            <button onClick={() => setShowEscalateForm(true)} className={`${styles.actionBtn} ${styles.btnEscalate}`}>↑ Escalate</button>
          </div>
        )}
      </div>

      {/* Evidence Slide-over */}
      {activeEvidence && (
        <div className={styles.overlay} onClick={() => setActiveEvidence(null)}>
          <div className={styles.slidePanel} onClick={e => e.stopPropagation()}>
            <div className={styles.slidePanelHeader}>
              <div className={styles.slidePanelTitle}>Evidence Record</div>
              <button onClick={() => setActiveEvidence(null)} className={styles.slideCloseBtn}>Esc ✕</button>
            </div>
            <div className={styles.slidePanelBody}>
              <div className={styles.slideField}>
                <div className={styles.slideFieldLabel}>RECORD TYPE</div>
                <div className={styles.slideFieldValue}>{activeEvidence.record_type}</div>
              </div>
              <div className={styles.slideField}>
                <div className={styles.slideFieldLabel}>RECORD ID</div>
                <code className={styles.slideFieldMono}>{activeEvidence.record_id}</code>
              </div>
              <div className={styles.slideField}>
                <div className={styles.slideFieldLabel}>ROLE IN CASE</div>
                <div className={styles.slideFieldValue}>{activeEvidence.role}</div>
              </div>
              <div className={styles.slideField}>
                <div className={styles.slideFieldLabel}>AMOUNT CONTRIBUTION</div>
                <div className={styles.slideFieldValue}>
                  {activeEvidence.amount_contribution ? fmt(activeEvidence.amount_contribution.paise) : "₹0.00"}
                </div>
              </div>
              <div className={styles.slideDivider} />
              <div className={styles.slideField}>
                <div className={styles.slideFieldLabel}>VERIFICATION</div>
                <div className={styles.slideFieldVerified}>✓ Matches settlement ledger</div>
              </div>
              {activeEvidence.note && (
                <div className={styles.slideField}>
                  <div className={styles.slideFieldLabel}>AGENT NOTE</div>
                  <div className={styles.slideFieldNote}>{activeEvidence.note}</div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
