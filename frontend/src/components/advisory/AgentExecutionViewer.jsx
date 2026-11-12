import React, { useState } from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  HelpCircle,
  XCircle,
  Clock,
  Cpu,
  ChevronDown,
  ChevronUp,
  Sparkles,
  CloudSun,
  Sprout,
  TrendingUp,
  Landmark,
  ShieldAlert,
} from 'lucide-react';
import { Badge } from '../ui/Badge';
import { AGENT_DISPLAY_NAMES, AGENT_DESCRIPTIONS } from '../../utils/constants';

const AGENT_ORDER = [
  'Intake',
  'Weather',
  'Crop',
  'Market',
  'Scheme',
  'Risk Alert',
  'Coordinator',
];

const AGENT_ICONS = {
  Intake: Sparkles,
  Weather: CloudSun,
  Crop: Sprout,
  Market: TrendingUp,
  Scheme: Landmark,
  'Risk Alert': ShieldAlert,
  Coordinator: Cpu,
};

export const AgentExecutionViewer = ({ agentResults = [], boardState }) => {
  const [expandedAgent, setExpandedAgent] = useState(null);

  // Index agent results by normalized ID
  const resultMap = {};
  agentResults.forEach((ar) => {
    const key = ar.agent_id.replace('RiskAlert', 'Risk Alert');
    resultMap[key] = ar;
  });

  const getStatusIcon = (status) => {
    switch (status?.toLowerCase()) {
      case 'completed':
        return <CheckCircle2 className="w-4 h-4 text-emerald-600" />;
      case 'partial':
        return <AlertTriangle className="w-4 h-4 text-amber-500" />;
      case 'unavailable':
        return <HelpCircle className="w-4 h-4 text-slate-400" />;
      case 'failed':
        return <XCircle className="w-4 h-4 text-red-500" />;
      default:
        return <Clock className="w-4 h-4 text-slate-400" />;
    }
  };

  const getStatusBadge = (status) => {
    switch (status?.toLowerCase()) {
      case 'completed':
        return <Badge variant="completed" size="sm">Completed</Badge>;
      case 'partial':
        return <Badge variant="partial" size="sm">Partial</Badge>;
      case 'unavailable':
        return <Badge variant="unavailable" size="sm">Unavailable</Badge>;
      case 'failed':
        return <Badge variant="failed" size="sm">Failed</Badge>;
      default:
        return <Badge variant="neutral" size="sm">Pending</Badge>;
    }
  };

  const totalDuration = agentResults.reduce((acc, curr) => acc + (curr.duration_ms || 0), 0);
  const completedCount = agentResults.filter((r) => r.status?.toLowerCase() === 'completed').length;

  return (
    <div className="bg-white rounded-2xl border border-[#E6EAE4] p-6 shadow-xs">
      {/* Header bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-5 mb-5 border-b border-[#E6EAE4] gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h3 className="text-base font-semibold text-[#202B24]">
              Seven-Agent Workflow Execution
            </h3>
            {boardState && (
              <Badge variant="active" size="sm">Board: {boardState}</Badge>
            )}
          </div>
          <p className="text-xs text-[#78837B] mt-0.5">
            Collaborative multi-agent reasoning graph powered by Microsoft Agent Framework
          </p>
        </div>

        <div className="flex items-center gap-4 text-xs font-medium text-[#78837B]">
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            <span>{completedCount}/7 Completed</span>
          </div>
          {totalDuration > 0 && (
            <div className="flex items-center gap-1 bg-[#F7F8F4] px-2.5 py-1 rounded-lg border border-[#E6EAE4]">
              <Clock className="w-3.5 h-3.5 text-[#347A50]" />
              <span>{Math.round(totalDuration)} ms total</span>
            </div>
          )}
        </div>
      </div>

      {/* Stepper Grid (Horizontal on lg, Stacked on mobile) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-7 gap-3">
        {AGENT_ORDER.map((agentName, idx) => {
          const result = resultMap[agentName] || {
            agent_id: agentName,
            status: 'unavailable',
            duration_ms: null,
            summary: 'No execution record returned',
          };

          const Icon = AGENT_ICONS[agentName] || Cpu;
          const isExpanded = expandedAgent === agentName;
          const isCoordinator = agentName === 'Coordinator';

          return (
            <div
              key={agentName}
              onClick={() => setExpandedAgent(isExpanded ? null : agentName)}
              className={`rounded-xl border transition-all duration-150 cursor-pointer p-3.5 flex flex-col justify-between ${
                isCoordinator
                  ? 'border-[#347A50]/50 bg-[#F7F8F4] hover:border-[#183D2B]'
                  : 'border-[#E6EAE4] bg-white hover:border-[#347A50]/40'
              } ${isExpanded ? 'ring-2 ring-[#347A50]/20' : ''}`}
            >
              <div>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] font-bold text-[#78837B]">0{idx + 1}</span>
                    <Icon className="w-4 h-4 text-[#347A50]" />
                  </div>
                  {getStatusIcon(result.status)}
                </div>

                <p className="text-xs font-semibold text-[#202B24] truncate">
                  {AGENT_DISPLAY_NAMES[agentName] || agentName}
                </p>

                <div className="mt-1 flex items-center justify-between">
                  {getStatusBadge(result.status)}
                  {result.duration_ms && (
                    <span className="text-[10px] text-[#78837B]">
                      {Math.round(result.duration_ms)}ms
                    </span>
                  )}
                </div>
              </div>

              <div className="mt-3 pt-2 border-t border-[#E6EAE4]/60 flex items-center justify-between text-[11px] text-[#78837B]">
                <span className="truncate max-w-[85px]">{result.provider_id || 'System'}</span>
                {isExpanded ? (
                  <ChevronUp className="w-3.5 h-3.5 shrink-0" />
                ) : (
                  <ChevronDown className="w-3.5 h-3.5 shrink-0" />
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Expanded Detail Tray */}
      {expandedAgent && (
        <div className="mt-4 p-4 rounded-xl border border-[#347A50]/30 bg-[#F7F8F4] animate-in fade-in duration-150">
          <div className="flex items-start justify-between">
            <div>
              <div className="flex items-center gap-2">
                <h4 className="text-sm font-semibold text-[#183D2B]">
                  {AGENT_DISPLAY_NAMES[expandedAgent] || expandedAgent}
                </h4>
                {getStatusBadge(resultMap[expandedAgent]?.status)}
              </div>
              <p className="text-xs text-[#78837B] mt-0.5">
                {AGENT_DESCRIPTIONS[expandedAgent]}
              </p>
            </div>
            <button
              onClick={() => setExpandedAgent(null)}
              className="text-xs font-medium text-[#347A50] hover:underline"
            >
              Close
            </button>
          </div>

          <div className="mt-3 pt-3 border-t border-[#E6EAE4] space-y-2 text-xs">
            {resultMap[expandedAgent]?.summary && (
              <div>
                <span className="font-medium text-[#202B24]">Output Summary: </span>
                <span className="text-[#202B24]">{resultMap[expandedAgent].summary}</span>
              </div>
            )}
            {resultMap[expandedAgent]?.error && (
              <div className="text-red-700 bg-red-50 p-2.5 rounded-lg border border-red-200">
                <span className="font-semibold">Reason: </span>
                {resultMap[expandedAgent].error}
              </div>
            )}
            <div className="flex flex-wrap gap-4 text-[11px] text-[#78837B] pt-1">
              <span>Provider: <strong className="text-[#202B24]">{resultMap[expandedAgent]?.provider_id || 'Deterministic/Default'}</strong></span>
              {resultMap[expandedAgent]?.model_id && (
                <span>Model: <strong className="text-[#202B24]">{resultMap[expandedAgent].model_id}</strong></span>
              )}
              {resultMap[expandedAgent]?.duration_ms && (
                <span>Duration: <strong className="text-[#202B24]">{Math.round(resultMap[expandedAgent].duration_ms)}ms</strong></span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
