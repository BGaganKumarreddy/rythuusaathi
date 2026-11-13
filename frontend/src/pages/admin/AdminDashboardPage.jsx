import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  ShieldCheck,
  Users,
  FileText,
  KeyRound,
  Cpu,
  Server,
  ArrowRight,
  RefreshCw,
  AlertCircle,
  Activity,
  CheckCircle2,
} from 'lucide-react';
import { adminService } from '../../services/adminService';
import { useAuth } from '../../context/AuthContext';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { Alert, Skeleton } from '../../components/ui/Feedback';

export const AdminDashboardPage = () => {
  const { user } = useAuth();
  const [data, setData] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  const loadData = async () => {
    setIsLoading(true);
    setError('');
    try {
      const res = await adminService.getDashboard();
      setData(res);
    } catch (err) {
      setError(err?.message || 'Failed to load administrative telemetry.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  return (
    <div className="space-y-6">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Badge variant="active" size="sm">System Administration</Badge>
            <span className="text-xs text-[#78837B]">• Tenant: {user?.tenant_id || 'default'}</span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
            Tenant Control & Telemetry Center
          </h1>
          <p className="text-sm text-[#78837B] mt-1">
            Monitor tenant member activity, agent execution performance, and system health.
          </p>
        </div>

        <Button variant="outline" size="sm" icon={RefreshCw} onClick={loadData} disabled={isLoading}>
          Refresh Metrics
        </Button>
      </div>

      {error && <Alert type="error" title="Telemetry Error" message={error} />}

      {/* Metric Cards */}
      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-28 rounded-2xl" />
          ))}
        </div>
      ) : data ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold text-[#78837B] uppercase tracking-wider">
                  Registered Farmers
                </p>
                <p className="text-3xl font-extrabold text-[#202B24] mt-1">
                  {data.total_users}
                </p>
                <p className="text-xs text-[#347A50] mt-1 font-medium">Tenant Members</p>
              </div>
              <div className="w-12 h-12 rounded-2xl bg-[#DCE9DC] flex items-center justify-center text-[#183D2B]">
                <Users className="w-6 h-6" />
              </div>
            </div>
          </Card>

          <Card>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold text-[#78837B] uppercase tracking-wider">
                  Consultations Logged
                </p>
                <p className="text-3xl font-extrabold text-[#202B24] mt-1">
                  {data.total_consultations}
                </p>
                <p className="text-xs text-[#347A50] mt-1 font-medium">Seven-agent Executions</p>
              </div>
              <div className="w-12 h-12 rounded-2xl bg-blue-100 flex items-center justify-center text-blue-800">
                <FileText className="w-6 h-6" />
              </div>
            </div>
          </Card>

          <Card>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold text-[#78837B] uppercase tracking-wider">
                  Active BYOK Keys
                </p>
                <p className="text-3xl font-extrabold text-[#202B24] mt-1">
                  {data.active_credentials}
                </p>
                <p className="text-xs text-amber-700 mt-1 font-medium">AES-256 Encrypted</p>
              </div>
              <div className="w-12 h-12 rounded-2xl bg-amber-100 flex items-center justify-center text-amber-800">
                <KeyRound className="w-6 h-6" />
              </div>
            </div>
          </Card>

          <Card>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs font-semibold text-[#78837B] uppercase tracking-wider">
                  System Status
                </p>
                <p className="text-2xl font-extrabold text-[#347A50] mt-1 capitalize">
                  {data.system_status}
                </p>
                <p className="text-xs text-[#78837B] mt-1 font-medium">7 Agents Online</p>
              </div>
              <div className="w-12 h-12 rounded-2xl bg-emerald-100 flex items-center justify-center text-emerald-800">
                <Activity className="w-6 h-6" />
              </div>
            </div>
          </Card>
        </div>
      ) : null}

      {/* Quick Nav Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <Card>
          <div className="space-y-4">
            <div className="w-10 h-10 rounded-xl bg-[#DCE9DC] flex items-center justify-center text-[#183D2B]">
              <Users className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-[#202B24]">User Management</h3>
              <p className="text-xs text-[#78837B] mt-1">
                Audit tenant memberships, inspect registered accounts, and verify role allocations.
              </p>
            </div>
            <Link
              to="/admin/users"
              className="inline-flex items-center gap-1.5 text-xs font-bold text-[#347A50] hover:text-[#183D2B] transition-colors"
            >
              <span>Manage Users</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </Card>

        <Card>
          <div className="space-y-4">
            <div className="w-10 h-10 rounded-xl bg-purple-100 flex items-center justify-center text-purple-800">
              <Cpu className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-[#202B24]">Agent Telemetry</h3>
              <p className="text-xs text-[#78837B] mt-1">
                Execution latencies, success rates, and token allocations for all 7 advisory agents.
              </p>
            </div>
            <Link
              to="/admin/agents"
              className="inline-flex items-center gap-1.5 text-xs font-bold text-[#347A50] hover:text-[#183D2B] transition-colors"
            >
              <span>View Agent Telemetry</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </Card>

        <Card>
          <div className="space-y-4">
            <div className="w-10 h-10 rounded-xl bg-slate-100 flex items-center justify-center text-slate-800">
              <Server className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-bold text-[#202B24]">System Specifications</h3>
              <p className="text-xs text-[#78837B] mt-1">
                Runtime configuration, Argon2id security parameters, and registry sizing.
              </p>
            </div>
            <Link
              to="/admin/system"
              className="inline-flex items-center gap-1.5 text-xs font-bold text-[#347A50] hover:text-[#183D2B] transition-colors"
            >
              <span>Inspect Runtime</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>
        </Card>
      </div>
    </div>
  );
};

