import React, { useState, useEffect } from 'react';
import { TrendingUp, Store, Sparkles } from 'lucide-react';
import { Link } from 'react-router-dom';
import { historyService } from '../../services/historyService';
import { advisoryService } from '../../services/advisoryService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { MarketEvidencePanel } from '../../components/advisory/MarketEvidencePanel';
import { CardSkeleton } from '../../components/ui/Feedback';

export const MarketViewPage = () => {
  const [marketEvidence, setMarketEvidence] = useState([]);
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
            if (adv.market_evidence?.length > 0) {
              if (isMounted) setMarketEvidence(adv.market_evidence);
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
            <Badge variant="active" size="sm">Agmarknet Feed</Badge>
            <span className="text-xs text-[#78837B]">• APMC Wholesale Mandi Prices</span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
            Mandi Commodity Prices & Market Intelligence
          </h1>
          <p className="text-sm text-[#78837B] mt-0.5">
            Daily wholesale commodity prices, minimum, maximum, and modal rates from official APMC trading floors
          </p>
        </div>

        <Link to="/advisories/new">
          <Button variant="primary" size="sm" icon={Sparkles}>
            Check Commodity Rates
          </Button>
        </Link>
      </div>

      {isLoading ? (
        <CardSkeleton />
      ) : (
        <MarketEvidencePanel marketEvidence={marketEvidence} />
      )}

      {/* Market Caution Note */}
      <Card padding="p-5" className="bg-[#F7F8F4] border-dashed">
        <h4 className="text-xs font-bold text-[#183D2B] mb-1">
          Trading & Price Caution Policy
        </h4>
        <p className="text-xs text-[#78837B] leading-relaxed">
          Daily APMC observations report modal rates for verified trades on the reporting date. A single day&apos;s price observation is insufficient to infer long-term demand trends; the Market Agent reports observations alongside trend cautions without fabricating speculative price forecasts.
        </p>
      </Card>
    </div>
  );
};
