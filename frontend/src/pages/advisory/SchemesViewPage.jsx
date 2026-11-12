import React, { useState, useEffect } from 'react';
import { Landmark, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';
import { historyService } from '../../services/historyService';
import { advisoryService } from '../../services/advisoryService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { SchemeEvidencePanel } from '../../components/advisory/SchemeEvidencePanel';
import { CardSkeleton } from '../../components/ui/Feedback';

export const SchemesViewPage = () => {
  const [schemeEvidence, setSchemeEvidence] = useState([]);
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
            if (adv.scheme_evidence?.length > 0) {
              if (isMounted) setSchemeEvidence(adv.scheme_evidence);
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
            <Badge variant="active" size="sm">Curated Scheme Corpus</Badge>
            <span className="text-xs text-[#78837B]">• Central & State Government Support</span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
            Government Welfare Schemes & Subsidies
          </h1>
          <p className="text-sm text-[#78837B] mt-0.5">
            PM-KISAN, PMFBY crop insurance, and state agricultural subsidy programs cross-referenced with your field context
          </p>
        </div>

        <Link to="/advisories/new">
          <Button variant="primary" size="sm" icon={Sparkles}>
            Check Scheme Eligibility
          </Button>
        </Link>
      </div>

      {isLoading ? (
        <CardSkeleton />
      ) : (
        <SchemeEvidencePanel schemeEvidence={schemeEvidence} />
      )}

      {/* Scheme Truth Policy Note */}
      <Card padding="p-5" className="bg-[#F7F8F4] border-dashed">
        <h4 className="text-xs font-bold text-[#183D2B] mb-1">
          Eligibility Verification Policy
        </h4>
        <p className="text-xs text-[#78837B] leading-relaxed">
          The Scheme Agent never claims a farmer is conclusively approved for a subsidy unless exact statutory eligibility criteria (landholding size, Aadhaar-linked bank seeding, and active season enrollment) are verified against official scheme portals.
        </p>
      </Card>
    </div>
  );
};
