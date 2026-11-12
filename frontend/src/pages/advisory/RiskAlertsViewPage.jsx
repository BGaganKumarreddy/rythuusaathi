import React, { useState, useEffect } from 'react';
import { AlertTriangle, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';
import { historyService } from '../../services/historyService';
import { advisoryService } from '../../services/advisoryService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { RiskAlertsPanel } from '../../components/advisory/RiskAlertsPanel';
import { CardSkeleton } from '../../components/ui/Feedback';

export const RiskAlertsViewPage = () => {
  const [activeAlerts, setActiveAlerts] = useState([]);
  const [bgVulnerabilities, setBgVulnerabilities] = useState([]);
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
            if (adv.active_risk_alerts?.length > 0 || adv.background_vulnerabilities?.length > 0) {
              if (isMounted) {
                setActiveAlerts(adv.active_risk_alerts || []);
                setBgVulnerabilities(adv.background_vulnerabilities || []);
              }
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
            <Badge variant="active" size="sm">Dual-Tier Risk Classification</Badge>
            <span className="text-xs text-[#78837B]">• Active Hazards vs Background Susceptibilities</span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
            Agricultural Hazard & Risk Intelligence
          </h1>
          <p className="text-sm text-[#78837B] mt-0.5">
            Real-time screening for excess rain, temperature stress, and evidence-grounded pest outbreaks
          </p>
        </div>

        <Link to="/advisories/new">
          <Button variant="primary" size="sm" icon={Sparkles}>
            Screen Field Risks
          </Button>
        </Link>
      </div>

      {isLoading ? (
        <CardSkeleton />
      ) : (
        <RiskAlertsPanel
          activeAlerts={activeAlerts}
          backgroundVulnerabilities={bgVulnerabilities}
        />
      )}
    </div>
  );
};

