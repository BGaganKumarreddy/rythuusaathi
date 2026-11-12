import React, { useState } from 'react';
import {
  AlertTriangle,
  ShieldCheck,
  ChevronDown,
  ChevronUp,
  Info,
  CheckCircle,
  AlertCircle,
  Lightbulb,
} from 'lucide-react';
import { Badge } from '../ui/Badge';
import { SEVERITY_COLORS } from '../../utils/constants';

export const RiskAlertsPanel = ({
  activeAlerts = [],
  backgroundVulnerabilities = [],
}) => {
  const [showBackground, setShowBackground] = useState(false);

  return (
    <div className="space-y-6">
      {/* 1. ACTIVE RISK ALERTS SECTION */}
      <div className="bg-white rounded-2xl border border-[#E6EAE4] p-6 shadow-xs">
        <div className="flex items-center justify-between pb-4 border-b border-[#E6EAE4]">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-amber-50 text-amber-600 flex items-center justify-center border border-amber-200">
              <AlertTriangle className="w-5 h-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="font-semibold text-base text-[#202B24]">
                  Active Agricultural Risk Alerts
                </h3>
                <span className="text-xs px-2 py-0.5 rounded-full font-bold bg-amber-100 text-amber-900 border border-amber-200">
                  {activeAlerts.length}
                </span>
              </div>
              <p className="text-xs text-[#78837B] mt-0.5">
                Evidence-grounded immediate threats requiring farmer attention and field mitigation
              </p>
            </div>
          </div>
        </div>

        <div className="mt-5 space-y-4">
          {activeAlerts.length === 0 ? (
            <div className="p-6 text-center rounded-xl bg-emerald-50/50 border border-emerald-200 flex flex-col items-center">
              <ShieldCheck className="w-8 h-8 text-emerald-600 mb-2" />
              <p className="text-sm font-semibold text-emerald-900">
                No Active Agricultural Hazards Detected
              </p>
              <p className="text-xs text-emerald-700/80 max-w-sm mt-0.5">
                Current weather observations and local market data do not indicate elevated immediate risks.
              </p>
            </div>
          ) : (
            activeAlerts.map((alert, idx) => {
              const sev = alert.severity?.toUpperCase() || 'MEDIUM';
              const sevConfig = SEVERITY_COLORS[sev] || SEVERITY_COLORS.MEDIUM;

              return (
                <div
                  key={alert.finding_id || idx}
                  className={`p-4 rounded-xl border ${sevConfig.bg} ${sevConfig.border} transition-all`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2">
                      <span className={`w-2.5 h-2.5 rounded-full ${sevConfig.dot}`} />
                      <h4 className="font-semibold text-sm text-[#202B24]">{alert.title}</h4>
                      <Badge variant={sev.toLowerCase()} size="sm">{sev}</Badge>
                    </div>

                    <div className="flex items-center gap-2 text-[11px] text-[#78837B]">
                      {alert.category && (
                        <span className="bg-white/80 px-2 py-0.5 rounded border border-[#E6EAE4]">
                          Category: {alert.category.replace('_', ' ')}
                        </span>
                      )}
                      {alert.confidence && (
                        <span>Confidence: {Math.round(alert.confidence * 100)}%</span>
                      )}
                    </div>
                  </div>

                  {/* Explanation */}
                  <p className="text-xs text-[#202B24] leading-relaxed mb-3">
                    {alert.explanation}
                  </p>

                  {/* Recommended Safe Action */}
                  {alert.safe_action && (
                    <div className="flex items-start gap-2 bg-white/90 p-3 rounded-lg border border-[#E6EAE4] text-xs">
                      <Lightbulb className="w-4 h-4 text-[#347A50] shrink-0 mt-0.5" />
                      <div>
                        <span className="font-semibold text-[#183D2B]">Recommended Safe Action: </span>
                        <span className="text-[#202B24]">{alert.safe_action}</span>
                      </div>
                    </div>
                  )}

                  {/* Evidence IDs */}
                  {alert.evidence_ids?.length > 0 && (
                    <div className="mt-2.5 flex items-center gap-1.5 text-[10px] text-[#78837B]">
                      <span>Evidence Grounding:</span>
                      {alert.evidence_ids.map((id) => (
                        <span key={id} className="bg-white px-1.5 py-0.5 rounded border border-[#E6EAE4]">
                          {id}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* 2. BACKGROUND VULNERABILITIES SECTION (COLLAPSIBLE) */}
      <div className="bg-white rounded-2xl border border-[#E6EAE4] p-5 shadow-xs">
        <button
          onClick={() => setShowBackground(!showBackground)}
          className="flex items-center justify-between w-full text-left"
        >
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-[#DCE9DC] text-[#183D2B] flex items-center justify-center">
              <Info className="w-4 h-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h4 className="font-semibold text-sm text-[#202B24]">
                  General Crop Susceptibility & Background Vulnerabilities
                </h4>
                <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">
                  {backgroundVulnerabilities.length} items
                </span>
              </div>
              <p className="text-[11px] text-[#78837B]">
                Baseline agronomic vulnerabilities from ICAR guidelines — NOT active field outbreaks
              </p>
            </div>
          </div>
          <div className="text-[#78837B] p-1">
            {showBackground ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </div>
        </button>

        {showBackground && (
          <div className="mt-4 pt-4 border-t border-[#E6EAE4] space-y-3">
            {backgroundVulnerabilities.length === 0 ? (
              <p className="text-xs text-[#78837B] italic">No background vulnerabilities recorded for this crop stage.</p>
            ) : (
              backgroundVulnerabilities.map((bg, idx) => (
                <div
                  key={bg.finding_id || idx}
                  className="p-3.5 rounded-xl border border-slate-200 bg-[#F7F8F4] text-xs"
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-semibold text-[#202B24]">{bg.title}</span>
                    <Badge variant="low" size="sm">Advisory Only</Badge>
                  </div>
                  <p className="text-[#78837B] mb-2 leading-relaxed">{bg.explanation}</p>
                  {bg.safe_action && (
                    <div className="text-[11px] text-[#183D2B] bg-white p-2 rounded-lg border border-[#E6EAE4]">
                      <strong>Preventative Note: </strong>{bg.safe_action}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
};

