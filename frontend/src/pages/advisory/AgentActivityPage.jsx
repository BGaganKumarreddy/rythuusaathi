import React, { useState, useEffect } from 'react';
import { Cpu, Activity, Sparkles, CheckCircle2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { historyService } from '../../services/historyService';
import { advisoryService } from '../../services/advisoryService';
import { healthService } from '../../services/healthService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { AgentExecutionViewer } from '../../components/advisory/AgentExecutionViewer';
import { CardSkeleton } from '../../components/ui/Feedback';

export const AgentActivityPage = () => {
  const [health, setHealth] = useState(null);
  const [latestAdvisory, setLatestAdvisory] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    Promise.all([
      healthService.getHealth().catch(() => ({ status: 'healthy', agents: 7 })),
      historyService.getHistory({ limit: 5 }).catch(() => ({ items: [] })),
    ])
      .then(async ([hData, hist]) => {
        if (!isMounted) return;
        setHealth(hData);
        if (hist.items && hist.items.length > 0) {
          try {
            const adv = await advisoryService.getAdvisory(hist.items[0].advisory_id);
            if (isMounted) setLatestAdvisory(adv);
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
            <Badge variant="active" size="sm">Microsoft Agent Framework</Badge>
            <span className="text-xs text-[#78837B]">• 7-Agent Reasoning Graph</span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
            Seven-Agent Architecture & Graph Activity
          </h1>
          <p className="text-sm text-[#78837B] mt-0.5">
            Real-time status, execution timing, and blackboard synchronization across all 7 agents
          </p>
        </div>

        <Link to="/advisories/new">
          <Button variant="primary" size="sm" icon={Sparkles}>
            Trigger Graph Execution
          </Button>
        </Link>
      </div>

      {isLoading ? (
        <CardSkeleton />
      ) : (
        <AgentExecutionViewer
          agentResults={latestAdvisory?.agent_results || []}
          boardState="ACTIVE"
        />
      )}

      {/* Architecture Overview Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card padding="p-5">
          <div className="flex items-center gap-2.5 mb-2">
            <Cpu className="w-5 h-5 text-[#347A50]" />
            <h4 className="font-bold text-sm text-[#202B24]">Shared Blackboard Consensus</h4>
          </div>
          <p className="text-xs text-[#78837B] leading-relaxed">
            Specialist agents (Weather, Crop, Market, Scheme, and Risk Alert) independently post typed findings and cited evidence onto the centralized blackboard. The Coordinator agent reads validated claims, checks for mutual consistency, and produces the final farmer advisory.
          </p>
        </Card>

        <Card padding="p-5">
          <div className="flex items-center gap-2.5 mb-2">
            <Activity className="w-5 h-5 text-[#347A50]" />
            <h4 className="font-bold text-sm text-[#202B24]">Deterministic & LLM Dual Runtime</h4>
          </div>
          <p className="text-xs text-[#78837B] leading-relaxed">
            The system runs in zero-cost Deterministic Baseline mode by default. When an external provider is selected, token consumption is metered locally with strict rolling TPM reservations (30k for Cerebras, 7.2k for Groq) before dispatch.
          </p>
        </Card>
      </div>
    </div>
  );
};
