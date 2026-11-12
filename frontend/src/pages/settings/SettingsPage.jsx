import React from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Settings as SettingsIcon,
  User,
  Building,
  Shield,
  KeyRound,
  LogOut,
  Clock,
  Database,
  Cpu,
  Layers,
  CheckCircle2,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';

export const SettingsPage = () => {
  const { user, logout } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    toast.info('Session logged out successfully');
    navigate('/login');
  };

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="pb-4 border-b border-[#E6EAE4]">
        <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
          System & Account Settings
        </h1>
        <p className="text-sm text-[#78837B] mt-0.5">
          View your authenticated profile, tenant configuration, and cryptographic specifications
        </p>
      </div>

      {/* 1. Account Profile Card */}
      <Card
        title="Authenticated Identity Profile"
        subtitle="Argon2id verified server-side session"
        headerIcon={User}
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div className="p-3.5 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] space-y-1">
            <span className="text-[#78837B]">Subject / User ID:</span>
            <p className="font-mono font-bold text-[#202B24] truncate">{user?.user_id || 'N/A'}</p>
          </div>

          <div className="p-3.5 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] space-y-1">
            <span className="text-[#78837B]">Tenant ID:</span>
            <div className="flex items-center gap-2">
              <p className="font-semibold text-[#202B24]">{user?.tenant_id || 'default'}</p>
              <Badge variant="active" size="sm">Isolated</Badge>
            </div>
          </div>

          <div className="p-3.5 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] space-y-1">
            <span className="text-[#78837B]">Assigned Roles:</span>
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {(user?.roles || ['member']).map((r) => (
                <Badge key={r} variant="completed" size="sm">
                  {r}
                </Badge>
              ))}
            </div>
          </div>

          <div className="p-3.5 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4] space-y-1">
            <span className="text-[#78837B]">Session Expires At:</span>
            <p className="text-[#202B24] font-medium">
              {user?.expires_at ? new Date(user.expires_at).toLocaleString() : 'Active session'}
            </p>
          </div>
        </div>
      </Card>

      {/* 2. Cryptographic Security Specifications */}
      <Card
        title="Cryptographic Architecture Specifications"
        subtitle="Zero-knowledge BYOK security parameters"
        headerIcon={Shield}
      >
        <div className="space-y-3 text-xs leading-relaxed">
          <div className="flex items-start gap-3 p-3 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4]">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" />
            <div>
              <strong className="text-[#183D2B]">AES-256-GCM Envelope Encryption: </strong>
              <span>
                Data Encryption Keys (DEKs) protect individual credentials with 96-bit random nonces and tenant-bound Additional Authenticated Data (AAD).
              </span>
            </div>
          </div>

          <div className="flex items-start gap-3 p-3 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4]">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" />
            <div>
              <strong className="text-[#183D2B]">Windows Credential Manager KEK: </strong>
              <span>
                Key Encryption Keys (KEKs) are stored inside the OS vault. Ephemeral in-memory fallback is disabled in production to guarantee persistence.
              </span>
            </div>
          </div>

          <div className="flex items-start gap-3 p-3 bg-[#F7F8F4] rounded-xl border border-[#E6EAE4]">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" />
            <div>
              <strong className="text-[#183D2B]">Argon2id Hashing & Hashed Sessions: </strong>
              <span>
                Passwords use memory-hard Argon2id parameters. Database stores only SHA-256 digests of session tokens, mitigating token leakage on disk.
              </span>
            </div>
          </div>
        </div>
      </Card>

      {/* 3. Session Termination */}
      <Card
        title="Session Security"
        subtitle="Terminate active authentication token"
        headerIcon={LogOut}
      >
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <p className="text-xs text-[#78837B] max-w-md leading-relaxed">
            Logging out deletes the server-side session hash in SQLite and clears the local authentication token from memory.
          </p>
          <Button variant="danger" size="md" icon={LogOut} onClick={handleLogout}>
            Log Out Session
          </Button>
        </div>
      </Card>
    </div>
  );
};

