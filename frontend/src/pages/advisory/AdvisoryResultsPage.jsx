import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  FileText,
  Sparkles,
  ArrowLeft,
  Printer,
  Code,
  Share2,
  Calendar,
  Layers,
  MapPin,
  Sprout,
  Activity,
  AlertCircle,
  ExternalLink,
} from 'lucide-react';
import { advisoryService } from '../../services/advisoryService';
import { useToast } from '../../context/ToastContext';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { Modal } from '../../components/ui/Modal';
import { CardSkeleton, Alert } from '../../components/ui/Feedback';
import { AgentExecutionViewer } from '../../components/advisory/AgentExecutionViewer';
import { CoordinatorSynthesisPanel } from '../../components/advisory/CoordinatorSynthesisPanel';
import { RiskAlertsPanel } from '../../components/advisory/RiskAlertsPanel';
import { WeatherEvidencePanel } from '../../components/advisory/WeatherEvidencePanel';
import { MarketEvidencePanel } from '../../components/advisory/MarketEvidencePanel';
import { SchemeEvidencePanel } from '../../components/advisory/SchemeEvidencePanel';

export const AdvisoryResultsPage = () => {
  const { advisoryId } = useParams();
  const toast = useToast();

  const [advisory, setAdvisory] = useState(null);
  const [trace, setTrace] = useState(null);
  const [isTraceOpen, setIsTraceOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let isMounted = true;
    setIsLoading(true);
    setError('');

    advisoryService
      .getAdvisory(advisoryId)
      .then((data) => {
        if (isMounted) setAdvisory(data);
      })
      .catch((err) => {
        if (isMounted) {
          setError(err?.message || 'Failed to load advisory report');
          toast.error(err?.message || 'Advisory not found or unauthorized');
        }
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [advisoryId, toast]);

  const handleOpenTrace = async () => {
    setIsTraceOpen(true);
    if (!trace) {
      try {
        const traceData = await advisoryService.getAdvisoryTrace(advisoryId);
        setTrace(traceData);
      } catch (err) {
        toast.error('Could not load diagnostic execution trace');
      }
    }
  };

  const handlePrint = () => {
    window.print();
  };

  if (isLoading) {
    return (
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div className="space-y-2">
            <div className="h-6 w-32 bg-slate-200 rounded animate-pulse" />
            <div className="h-4 w-64 bg-slate-200 rounded animate-pulse" />
          </div>
        </div>
        <CardSkeleton />
        <CardSkeleton />
      </div>
    );
  }

  if (error || !advisory) {
    return (
      <div className="max-w-xl mx-auto py-12 text-center space-y-4">
        <Alert type="error" title="Advisory Unavailable">
          {error || 'Advisory consultation not found or not authorized'}
        </Alert>
        <Link to="/advisories">
          <Button variant="outline" size="sm" icon={ArrowLeft}>
            Back to Consultations
          </Button>
        </Link>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6 print:m-0 print:p-0">
      {/* Top action bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-[#E6EAE4] print:hidden">
        <div className="flex items-center gap-3">
          <Link to="/advisories">
            <button className="p-2 rounded-xl border border-[#E6EAE4] bg-white hover:bg-[#F7F8F4] text-[#78837B] transition-colors">
              <ArrowLeft className="w-4 h-4" />
            </button>
          </Link>
          <div>
            <div className="flex items-center gap-2">
              <Badge variant={advisory.status?.toLowerCase()} size="sm">
                {advisory.status}
              </Badge>
              <span className="text-xs text-[#78837B]">
                ID: {advisory.advisory_id}
              </span>
            </div>
            <h1 className="text-xl font-bold text-[#202B24] tracking-tight mt-0.5">
              Agricultural Advisory Intelligence Report
            </h1>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" icon={Code} onClick={handleOpenTrace}>
            Diagnostic Trace
          </Button>
          <Button variant="outline" size="sm" icon={Printer} onClick={handlePrint}>
            Print Report
          </Button>
          <Link to="/advisories/new">
            <Button variant="primary" size="sm" icon={Sparkles}>
              New Inquiry
            </Button>
          </Link>
        </div>
      </div>

      {/* 1. COORDINATOR SYNTHESIS PANEL */}
      <CoordinatorSynthesisPanel
        synthesis={advisory.coordinator_synthesis}
        suggestedNextSteps={advisory.suggested_next_steps}
        uncertaintyAndLimitations={advisory.uncertainty_and_limitations}
        status={advisory.status}
        providerId={advisory.provider_id}
        modelId={advisory.model_id}
        createdAt={advisory.created_at}
      />

      {/* 2. SEVEN-AGENT WORKFLOW EXECUTION STEPPER */}
      <AgentExecutionViewer
        agentResults={advisory.agent_results}
        boardState="SYNTHESIZED"
      />

      {/* 3. DUAL-TIER RISK ALERTS PANEL */}
      <RiskAlertsPanel
        activeAlerts={advisory.active_risk_alerts}
        backgroundVulnerabilities={advisory.background_vulnerabilities}
      />

      {/* 4. DOMAIN EVIDENCE SECTIONS (2 columns) */}
      <div className="grid grid-cols-1 gap-6">
        {/* Weather Evidence */}
        <WeatherEvidencePanel weatherEvidence={advisory.weather_evidence} />

        {/* Mandi Commodity Market Evidence */}
        <MarketEvidencePanel marketEvidence={advisory.market_evidence} />

        {/* Government Schemes Evidence */}
        <SchemeEvidencePanel schemeEvidence={advisory.scheme_evidence} />
      </div>

      {/* 5. DIAGNOSTIC TRACE MODAL */}
      <Modal
        isOpen={isTraceOpen}
        onClose={() => setIsTraceOpen(false)}
        title="Diagnostic Execution Trace"
        subtitle={`Advisory Consultation ID: ${advisory.advisory_id}`}
        maxWidth="max-w-3xl"
        footer={
          <Button variant="outline" size="sm" onClick={() => setIsTraceOpen(false)}>
            Close
          </Button>
        }
      >
        {trace ? (
          <div className="space-y-4 text-xs font-mono">
            <div className="p-3 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] space-y-1">
              <div><strong>Status:</strong> {trace.status}</div>
              <div><strong>Board State:</strong> {trace.board_state}</div>
              <div><strong>Conflicts Detected:</strong> {trace.conflicts?.length || 0}</div>
              <div><strong>Findings Logged:</strong> {trace.findings?.length || 0}</div>
            </div>

            <div>
              <h5 className="font-sans font-bold text-sm text-[#202B24] mb-2">Agent Executions:</h5>
              <div className="space-y-2">
                {(trace.agent_executions || []).map((ex, idx) => (
                  <div key={idx} className="p-2.5 rounded-lg border border-[#E6EAE4] bg-white flex items-center justify-between">
                    <div>
                      <strong className="text-[#183D2B]">{ex.agent_id}</strong>: {ex.status}
                    </div>
                    <span className="text-[#78837B]">{Math.round(ex.duration_ms || 0)}ms</span>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <h5 className="font-sans font-bold text-sm text-[#202B24] mb-2">Raw JSON Payload:</h5>
              <pre className="p-3 bg-slate-900 text-emerald-400 rounded-xl overflow-x-auto max-h-72 text-[11px] leading-relaxed">
                {JSON.stringify(trace, null, 2)}
              </pre>
            </div>
          </div>
        ) : (
          <div className="py-8 text-center text-xs text-[#78837B]">
            Loading execution trace records...
          </div>
        )}
      </Modal>
    </div>
  );
};

