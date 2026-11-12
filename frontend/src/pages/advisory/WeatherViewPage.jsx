import React, { useState, useEffect } from 'react';
import { CloudSun, Droplets, Wind, Thermometer, MapPin, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useConfig } from '../../context/ConfigContext';
import { historyService } from '../../services/historyService';
import { advisoryService } from '../../services/advisoryService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { WeatherEvidencePanel } from '../../components/advisory/WeatherEvidencePanel';
import { CardSkeleton } from '../../components/ui/Feedback';

export const WeatherViewPage = () => {
  const { locations } = useConfig();
  const [weatherEvidence, setWeatherEvidence] = useState([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    historyService
      .getHistory({ limit: 10 })
      .then(async (hist) => {
        if (!isMounted) return;
        for (const item of hist.items || []) {
          try {
            const adv = await advisoryService.getAdvisory(item.advisory_id);
            if (adv.weather_evidence?.length > 0) {
              if (isMounted) setWeatherEvidence(adv.weather_evidence);
              break;
            }
          } catch {
            // continue
          }
        }
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-[#E6EAE4]">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Badge variant="active" size="sm">Open-Meteo Integration</Badge>
            <span className="text-xs text-[#78837B]">• Live Meteorological Stream</span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
            Agricultural Weather Observations
          </h1>
          <p className="text-sm text-[#78837B] mt-0.5">
            Real-time verified temperature, humidity, precipitation accumulation, and 3-day agricultural forecast
          </p>
        </div>

        <Link to="/advisories/new">
          <Button variant="primary" size="sm" icon={Sparkles}>
            Query Field Weather
          </Button>
        </Link>
      </div>

      {isLoading ? (
        <CardSkeleton />
      ) : (
        <WeatherEvidencePanel weatherEvidence={weatherEvidence} />
      )}

      {/* Meteorological Architecture Note */}
      <Card padding="p-5" className="bg-[#F7F8F4] border-dashed">
        <h4 className="text-xs font-bold text-[#183D2B] mb-1">
          Grounding Methodology
        </h4>
        <p className="text-xs text-[#78837B] leading-relaxed">
          The Weather Agent queries the Open-Meteo REST API for geographic coordinates mapped to the farmer&apos;s administrative district. Soil moisture levels, precipitation forecasts, and wind velocities are extracted to compute real-time spray windows and excess rain risk alerts.
        </p>
      </Card>
    </div>
  );
};
