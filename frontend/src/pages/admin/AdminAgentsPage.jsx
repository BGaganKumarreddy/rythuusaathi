import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Cpu,
  Clock,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  ArrowLeft,
  Activity,
} from 'lucide-react';
import { adminService } from '../../services/adminService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { Alert, Skeleton } from '../../components/ui/Feedback';

export const AdminAgentsPage = () => {
  const [telemetry, setTelemetry] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  const loadTelemetry = async () => {
    setIsLoading(true);
    setError('');
    try {
      const res = await adminService.getAgents();
      setTelemetry(res || []);
    } catch (err) {
      setError(err?.message || 'Failed to retrieve agent telemetry.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadTelemetry();
  }, []);

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Link
              to="/admin"
              className="text-xs text-[#347A50] hover:underline flex items-center gap-1 font-semibold"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              <span>Back to Control Center</span>
            </Link>
          </div>
          <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
            Seven-Agent Telemetry & Execution Health
          </h1>
          <p className="text-sm text-[#78837B] mt-1">
            Audit runtime latency, status distributions, and execution reliability across all specialized agents.
          </p>
        </div>

        <Button variant="outline" size="sm" icon={RefreshCw} onClick={loadTelemetry} disabled={isLoading}>
          Refresh Telemetry
        </Button>
      </div>

      {error && <Alert type="error" title="Telemetry Failed" message={error} />}

      {/* Agents Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3, 4, 5, 6, 7].map((i) => (
            <Skeleton key={i} className="h-44 rounded-2xl" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {telemetry.map((item) => {
            const successRate =
              item.total_executions > 0
                ? Math.round((item.successful_executions / item.total_executions) * 100)
                : 100;

            return (
              <Card key={item.agent_id}>
                <div className="space-y-3">
                  <div className="flex items-center justify-between border-b border-[#E6EAE4] pb-2.5">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-lg bg-[#DCE9DC] flex items-center justify-center text-[#183D2B]">
                        <Cpu className="w-4 h-4" />
                      </div>
                      <div>
                        <h3 className="font-bold text-sm text-[#202B24]">{item.agent_id} Agent</h3>
                        <p className="text-[10px] text-[#78837B] uppercase font-semibold">Specialist</p>
                      </div>
                    </div>
                    <Badge variant={item.failed_executions > 0 ? 'critical' : 'active'} size="sm">
                      {item.failed_executions > 0 ? 'Warnings' : 'Healthy'}
                    </Badge>
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="bg-[#F7F8F4] p-2 rounded-lg">
                      <p className="text-[#78837B] font-medium">Total Runs</p>
                      <p className="text-base font-bold text-[#202B24] mt-0.5">{item.total_executions}</p>
                    </div>

                    <div className="bg-[#F7F8F4] p-2 rounded-lg">
                      <p className="text-[#78837B] font-medium">Avg Latency</p>
                      <p className="text-base font-bold text-[#202B24] mt-0.5 flex items-center gap-1">
                        <Clock className="w-3.5 h-3.5 text-[#347A50]" />
                        <span>{item.average_duration_ms} ms</span>
                      </p>
                    </div>
                  </div>

                  <div className="pt-1 flex items-center justify-between text-xs text-[#78837B]">
                    <span>Success Rate</span>
                    <span className="font-bold text-[#347A50]">{successRate}%</span>
                  </div>

                  <div className="w-full bg-[#E6EAE4] h-1.5 rounded-full overflow-hidden">
                    <div
                      className="bg-[#347A50] h-full rounded-full transition-all"
                      style={{ width: `${successRate}%` }}
                    />
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
};

