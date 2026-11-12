import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  Server,
  Shield,
  Database,
  Lock,
  Layers,
  CheckCircle2,
  ArrowLeft,
  RefreshCw,
  Cpu,
} from 'lucide-react';
import { adminService } from '../../services/adminService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { Alert, Skeleton } from '../../components/ui/Feedback';

export const AdminSystemPage = () => {
  const [system, setSystem] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  const loadSystem = async () => {
    setIsLoading(true);
    setError('');
    try {
      const res = await adminService.getSystem();
      setSystem(res);
    } catch (err) {
      setError(err?.message || 'Failed to inspect system specifications.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadSystem();
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
            System & Security Architecture Specifications
          </h1>
          <p className="text-sm text-[#78837B] mt-1">
            Cryptographic standards, database configuration, and agricultural catalog sizing.
          </p>
        </div>

        <Button variant="outline" size="sm" icon={RefreshCw} onClick={loadSystem} disabled={isLoading}>
          Refresh Specs
        </Button>
      </div>

      {error && <Alert type="error" title="Specifications Retrieval Failed" message={error} />}

      {isLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-48 rounded-2xl" />
          <Skeleton className="h-48 rounded-2xl" />
        </div>
      ) : system ? (
        <div className="space-y-6">
          <Card>
            <div className="space-y-4">
              <h2 className="text-base font-bold text-[#202B24] flex items-center gap-2 border-b border-[#E6EAE4] pb-3">
                <Lock className="w-5 h-5 text-[#347A50]" />
                <span>Cryptographic & Identity Architecture</span>
              </h2>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                <div className="p-3 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4]">
                  <p className="text-xs font-semibold text-[#78837B] uppercase">Authentication Security</p>
                  <p className="font-mono text-sm font-bold text-[#202B24] mt-1">{system.password_hash}</p>
                  <p className="text-xs text-[#78837B] mt-1">Zero plaintext storage; server-side SHA-256 session token digest</p>
                </div>

                <div className="p-3 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4]">
                  <p className="text-xs font-semibold text-[#78837B] uppercase">BYOK Credential Vault</p>
                  <p className="font-mono text-sm font-bold text-[#202B24] mt-1">{system.encryption_algorithm}</p>
                  <p className="text-xs text-[#78837B] mt-1">Key-encrypting-key (KEK) wrapped master keys with owner-only policy</p>
                </div>
              </div>
            </div>
          </Card>

          <Card>
            <div className="space-y-4">
              <h2 className="text-base font-bold text-[#202B24] flex items-center gap-2 border-b border-[#E6EAE4] pb-3">
                <Database className="w-5 h-5 text-[#347A50]" />
                <span>Agricultural Registries & Sizing</span>
              </h2>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="p-4 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] text-center">
                  <p className="text-xs font-semibold text-[#78837B] uppercase">Registered Crops</p>
                  <p className="text-3xl font-extrabold text-[#347A50] mt-1">{system.registered_crops_count}</p>
                  <p className="text-xs text-[#78837B] mt-1">Cited ICAR botanical records</p>
                </div>

                <div className="p-4 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] text-center">
                  <p className="text-xs font-semibold text-[#78837B] uppercase">Geographic Scopes</p>
                  <p className="text-3xl font-extrabold text-[#347A50] mt-1">{system.registered_locations_count}</p>
                  <p className="text-xs text-[#78837B] mt-1">State and district divisions</p>
                </div>

                <div className="p-4 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] text-center">
                  <p className="text-xs font-semibold text-[#78837B] uppercase">Validated Coverage Pairs</p>
                  <p className="text-3xl font-extrabold text-[#347A50] mt-1">{system.coverage_pairs_count}</p>
                  <p className="text-xs text-[#78837B] mt-1">Explicit crop-location pairs</p>
                </div>
              </div>
            </div>
          </Card>

          <Card>
            <div className="space-y-4">
              <h2 className="text-base font-bold text-[#202B24] flex items-center gap-2 border-b border-[#E6EAE4] pb-3">
                <Server className="w-5 h-5 text-[#347A50]" />
                <span>Runtime Environment</span>
              </h2>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
                <div>
                  <span className="text-[#78837B] font-medium">Framework:</span>
                  <p className="text-[#202B24] font-semibold mt-0.5">{system.framework}</p>
                </div>
                <div>
                  <span className="text-[#78837B] font-medium">Database Engine:</span>
                  <p className="text-[#202B24] font-semibold mt-0.5">{system.storage_engine}</p>
                </div>
                <div>
                  <span className="text-[#78837B] font-medium">Deployment Target:</span>
                  <p className="text-[#202B24] font-semibold mt-0.5">{system.environment}</p>
                </div>
              </div>
            </div>
          </Card>
        </div>
      ) : null}
    </div>
  );
};
