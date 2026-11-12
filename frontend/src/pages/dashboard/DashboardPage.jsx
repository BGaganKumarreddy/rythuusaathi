import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Sparkles,
  FileText,
  AlertTriangle,
  CloudSun,
  TrendingUp,
  Landmark,
  ShieldCheck,
  ArrowRight,
  Sprout,
  Activity,
  Droplets,
  Wind,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useConfig } from '../../context/ConfigContext';
import { historyService } from '../../services/historyService';
import { advisoryService } from '../../services/advisoryService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { Skeleton, CardSkeleton } from '../../components/ui/Feedback';

export const DashboardPage = () => {
  const { user } = useAuth();
  const { crops, locations, isLoading: configLoading } = useConfig();

  const [historyItems, setHistoryItems] = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [latestAdvisory, setLatestAdvisory] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;

    async function loadDashboardData() {
      try {
        const hist = await historyService.getHistory({ limit: 5, offset: 0 });
        if (!isMounted) return;

        setHistoryItems(hist.items || []);
        setTotalCount(hist.total || 0);

        if (hist.items && hist.items.length > 0) {
          try {
            const latest = await advisoryService.getAdvisory(hist.items[0].advisory_id);
            if (isMounted) setLatestAdvisory(latest);
          } catch {
            // Non-fatal if detailed retrieval fails
          }
        }
      } catch (err) {
        console.error('Failed to load dashboard data:', err);
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }

    loadDashboardData();

    return () => {
      isMounted = false;
    };
  }, []);

  const todayStr = new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });

  const activeAlerts = latestAdvisory?.active_risk_alerts || [];
  const weatherEvidence = latestAdvisory?.weather_evidence || [];
  const marketEvidence = latestAdvisory?.market_evidence || [];

  return (
    <div className="space-y-6">
      {/* 1. WELCOME HERO SECTION */}
      <div className="bg-gradient-to-r from-[#183D2B] via-[#23543b] to-[#347A50] rounded-2xl p-6 sm:p-8 text-white shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-[#DCE9DC]">
              {todayStr}
            </span>
            <span className="text-[#DCE9DC]/60">•</span>
            <span className="text-xs text-[#DCE9DC]/80 font-medium">
              Tenant: {user?.tenant_id || 'default'}
            </span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-extrabold tracking-tight">
            Welcome back, {user?.user_id?.split('-')[0] || 'Farmer'}
          </h1>
          <p className="text-sm text-[#DCE9DC]/90 max-w-xl leading-relaxed">
            Your seven-agent agricultural advisory board is active and synchronized with live weather observations, ICAR research bulletins, and APMC market prices.
          </p>
        </div>

        <div className="shrink-0">
          <Link to="/advisories/new">
            <Button
              variant="sage"
              size="lg"
              icon={Sparkles}
              className="font-semibold shadow-md text-[#183D2B]"
            >
              Start New Consultation
            </Button>
          </Link>
        </div>
      </div>

      {/* 2. TOP METRICS CARDS */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Metric 1 */}
        <Card padding="p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-medium text-[#78837B]">Total Consultations</span>
            <div className="w-8 h-8 rounded-lg bg-[#DCE9DC] text-[#183D2B] flex items-center justify-center">
              <FileText className="w-4 h-4" />
            </div>
          </div>
          {isLoading ? (
            <Skeleton className="h-8 w-16" />
          ) : (
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold text-[#202B24]">{totalCount}</span>
              <span className="text-xs text-[#78837B]">completed</span>
            </div>
          )}
        </Card>

        {/* Metric 2 */}
        <Card padding="p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-medium text-[#78837B]">Active Risk Alerts</span>
            <div className="w-8 h-8 rounded-lg bg-amber-100 text-amber-700 flex items-center justify-center">
              <AlertTriangle className="w-4 h-4" />
            </div>
          </div>
          {isLoading ? (
            <Skeleton className="h-8 w-16" />
          ) : (
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold text-[#202B24]">
                {activeAlerts.length}
              </span>
              <span className="text-xs text-[#78837B]">immediate hazards</span>
            </div>
          )}
        </Card>

        {/* Metric 3 */}
        <Card padding="p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-medium text-[#78837B]">Registered Crops</span>
            <div className="w-8 h-8 rounded-lg bg-[#DCE9DC] text-[#183D2B] flex items-center justify-center">
              <Sprout className="w-4 h-4" />
            </div>
          </div>
          {configLoading ? (
            <Skeleton className="h-8 w-16" />
          ) : (
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold text-[#202B24]">{crops.length}</span>
              <span className="text-xs text-[#78837B]">in catalog</span>
            </div>
          )}
        </Card>

        {/* Metric 4 */}
        <Card padding="p-5">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-medium text-[#78837B]">Agent Graph State</span>
            <div className="w-8 h-8 rounded-lg bg-emerald-100 text-emerald-800 flex items-center justify-center">
              <Activity className="w-4 h-4" />
            </div>
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-xl font-bold text-[#202B24]">7 Active</span>
            <Badge variant="completed" size="sm">Online</Badge>
          </div>
        </Card>
      </div>

      {/* 3. MAIN DASHBOARD GRID */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Recent Activity & Latest Consultation Summary */}
        <div className="lg:col-span-2 space-y-6">
          {/* Recent Consultations List */}
          <Card
            title="Recent Advisory Consultations"
            subtitle="Past multi-agent analyses and recommendations"
            headerIcon={FileText}
            action={
              <Link to="/advisories" className="text-xs font-semibold text-[#347A50] hover:underline flex items-center gap-1">
                <span>View all</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            }
          >
            {isLoading ? (
              <div className="space-y-3">
                <CardSkeleton />
              </div>
            ) : historyItems.length === 0 ? (
              <div className="text-center py-8">
                <FileText className="w-8 h-8 text-[#78837B] mx-auto mb-2 opacity-40" />
                <p className="text-sm font-medium text-[#202B24]">No consultations yet</p>
                <p className="text-xs text-[#78837B] mt-1 max-w-sm mx-auto">
                  Submit your first agronomic question to see multi-agent synthesis here.
                </p>
                <Link to="/advisories/new" className="inline-block mt-4">
                  <Button variant="primary" size="sm">
                    Create Advisory
                  </Button>
                </Link>
              </div>
            ) : (
              <div className="divide-y divide-[#E6EAE4]">
                {historyItems.map((item) => (
                  <Link
                    key={item.advisory_id}
                    to={`/advisories/${encodeURIComponent(item.advisory_id)}`}
                    className="py-3.5 flex items-start justify-between gap-4 hover:bg-[#F7F8F4] -mx-6 px-6 transition-colors first:pt-0 last:pb-0"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-semibold text-sm text-[#202B24] truncate">
                          {item.query}
                        </span>
                        <Badge variant={item.status?.toLowerCase()} size="sm">
                          {item.status}
                        </Badge>
                      </div>

                      <div className="flex flex-wrap items-center gap-2 text-xs text-[#78837B]">
                        {item.crop && (
                          <span className="bg-[#DCE9DC] text-[#183D2B] font-medium px-2 py-0.5 rounded-md text-[11px]">
                            {item.crop}
                          </span>
                        )}
                        {item.location && <span>Location: {item.location}</span>}
                        <span>•</span>
                        <span>{new Date(item.created_at).toLocaleDateString()}</span>
                        {item.active_alert_count > 0 && (
                          <span className="text-amber-700 font-semibold flex items-center gap-1">
                            <AlertTriangle className="w-3 h-3" />
                            {item.active_alert_count} alert{item.active_alert_count > 1 ? 's' : ''}
                          </span>
                        )}
                      </div>
                    </div>

                    <ArrowRight className="w-4 h-4 text-[#78837B] shrink-0 mt-1" />
                  </Link>
                ))}
              </div>
            )}
          </Card>

          {/* Latest Advisory Coordinator Preview if available */}
          {latestAdvisory && (
            <Card
              title="Latest Coordinator Advisory Preview"
              subtitle={`Consultation ID: ${latestAdvisory.advisory_id}`}
              headerIcon={Sparkles}
              action={
                <Link
                  to={`/advisories/${encodeURIComponent(latestAdvisory.advisory_id)}`}
                  className="text-xs font-semibold text-[#347A50] hover:underline"
                >
                  Full Report
                </Link>
              }
            >
              <div className="space-y-4">
                <div className="p-4 rounded-xl bg-[#F7F8F4] border border-[#E6EAE4] text-xs text-[#202B24] leading-relaxed line-clamp-4 whitespace-pre-line">
                  {latestAdvisory.coordinator_synthesis}
                </div>

                {latestAdvisory.suggested_next_steps?.length > 0 && (
                  <div>
                    <h5 className="text-xs font-semibold text-[#202B24] mb-2">Next Actions:</h5>
                    <ul className="space-y-1.5 text-xs text-[#78837B]">
                      {latestAdvisory.suggested_next_steps.slice(0, 3).map((step, idx) => (
                        <li key={idx} className="flex items-start gap-2">
                          <span className="w-1.5 h-1.5 rounded-full bg-[#347A50] mt-1.5 shrink-0" />
                          <span className="text-[#202B24]">{step}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </Card>
          )}
        </div>

        {/* Right 1 Col: Live Weather, Risk Alerts, Market highlights */}
        <div className="space-y-6">
          {/* Weather Widget */}
          <Card
            title="Meteorological Overview"
            subtitle="Verified observations via Open-Meteo"
            headerIcon={CloudSun}
            action={
              <Link to="/weather" className="text-xs font-semibold text-[#347A50] hover:underline">
                View
              </Link>
            }
          >
            {weatherEvidence.length > 0 ? (
              <div className="space-y-3">
                <div className="p-3 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] text-xs font-mono text-[#202B24]">
                  {weatherEvidence[0].excerpt || weatherEvidence[0].title}
                </div>
                <div className="flex items-center justify-between text-[11px] text-[#78837B]">
                  <span>Source: Open-Meteo Live</span>
                  <Badge variant="completed" size="sm">Grounded</Badge>
                </div>
              </div>
            ) : (
              <div className="py-4 text-center">
                <CloudSun className="w-8 h-8 text-[#78837B] mx-auto mb-1 opacity-50" />
                <p className="text-xs text-[#78837B]">
                  Weather observations are fetched in real-time when submitting a consultation with location.
                </p>
              </div>
            )}
          </Card>

          {/* Active Risk Alerts Widget */}
          <Card
            title="Active Hazards & Alerts"
            subtitle="Real-time evidence-grounded threats"
            headerIcon={AlertTriangle}
            action={
              <Link to="/risk-alerts" className="text-xs font-semibold text-[#347A50] hover:underline">
                All
              </Link>
            }
          >
            {activeAlerts.length > 0 ? (
              <div className="space-y-2.5">
                {activeAlerts.slice(0, 2).map((a, idx) => (
                  <div
                    key={idx}
                    className="p-3 rounded-xl border border-amber-200 bg-amber-50/60 text-xs"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-semibold text-amber-900">{a.title}</span>
                      <Badge variant={a.severity?.toLowerCase()} size="sm">{a.severity}</Badge>
                    </div>
                    <p className="text-[#78837B] text-[11px] line-clamp-2">{a.explanation}</p>
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-4 text-center">
                <ShieldCheck className="w-8 h-8 text-emerald-600 mx-auto mb-1" />
                <p className="text-xs font-semibold text-emerald-900">Zero Active Hazards</p>
                <p className="text-[11px] text-[#78837B] mt-0.5">
                  No immediate elevated weather or pest risks in current field context.
                </p>
              </div>
            )}
          </Card>

          {/* Quick Mandi Market Link */}
          <Card
            title="Mandi Market Rates"
            subtitle="Daily APMC wholesale observations"
            headerIcon={TrendingUp}
            action={
              <Link to="/market" className="text-xs font-semibold text-[#347A50] hover:underline">
                Explore
              </Link>
            }
          >
            {marketEvidence.length > 0 ? (
              <div className="p-3 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] text-xs font-mono text-[#202B24]">
                {marketEvidence[0].excerpt || marketEvidence[0].title}
              </div>
            ) : (
              <p className="text-xs text-[#78837B] py-2">
                Commodity prices are retrieved via official Agmarknet APMC feeds when querying crop marketing.
              </p>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};
