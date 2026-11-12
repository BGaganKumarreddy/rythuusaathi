import React from 'react';
import { CloudSun, Droplets, Wind, Thermometer, CloudRain, ExternalLink } from 'lucide-react';
import { Badge } from '../ui/Badge';

export const WeatherEvidencePanel = ({ weatherEvidence = [] }) => {
  if (!weatherEvidence || weatherEvidence.length === 0) {
    return (
      <div className="bg-white rounded-2xl border border-[#E6EAE4] p-6 text-center shadow-xs">
        <CloudSun className="w-8 h-8 text-[#78837B] mx-auto mb-2 opacity-50" />
        <h4 className="text-sm font-semibold text-[#202B24]">Weather Evidence Unavailable</h4>
        <p className="text-xs text-[#78837B] max-w-sm mx-auto mt-1">
          Open-Meteo observations were not available or this query did not require meteorological retrieval.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-2xl border border-[#E6EAE4] p-6 shadow-xs space-y-4">
      <div className="flex items-center justify-between pb-3 border-b border-[#E6EAE4]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-[#DCE9DC] text-[#183D2B] flex items-center justify-center">
            <CloudSun className="w-4 h-4" />
          </div>
          <div>
            <h3 className="font-semibold text-base text-[#202B24]">
              Meteorological Evidence & Observations
            </h3>
            <p className="text-xs text-[#78837B]">Open-Meteo verified live observations and forecast</p>
          </div>
        </div>
        <Badge variant="active" size="sm">Live Feed</Badge>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {weatherEvidence.map((ev, idx) => (
          <div
            key={ev.evidence_id || idx}
            className="p-4 rounded-xl border border-[#E6EAE4] bg-[#F7F8F4] flex flex-col justify-between"
          >
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-[#183D2B] truncate">{ev.title}</span>
                {ev.retrieved_at && (
                  <span className="text-[10px] text-[#78837B]">
                    {new Date(ev.retrieved_at).toLocaleDateString()}
                  </span>
                )}
              </div>

              {ev.excerpt && (
                <div className="p-3 bg-white rounded-lg border border-[#E6EAE4] text-xs font-mono text-[#202B24] leading-relaxed mb-3 break-words">
                  {ev.excerpt}
                </div>
              )}
            </div>

            <div className="flex flex-wrap items-center justify-between text-[11px] text-[#78837B] pt-2 border-t border-[#E6EAE4]/60 gap-2">
              <span className="truncate">Source: {ev.provenance || ev.source_id}</span>
              {ev.reference_url && (
                <a
                  href={ev.reference_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-[#347A50] hover:underline"
                >
                  <span>Verify</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

