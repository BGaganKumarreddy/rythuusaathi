import React, { useState, useEffect, useCallback } from 'react';
import {
  KeyRound,
  Plus,
  Trash2,
  ShieldCheck,
  Lock,
  Cpu,
  AlertCircle,
  ExternalLink,
  CheckCircle2,
  Clock,
} from 'lucide-react';
import { credentialService } from '../../services/credentialService';
import { useToast } from '../../context/ToastContext';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Badge } from '../../components/ui/Badge';
import { Input, Select } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';
import { EmptyState, CardSkeleton, Alert } from '../../components/ui/Feedback';

const PROVIDER_METADATA = {
  cerebras: {
    name: 'Cerebras Cloud Inference',
    model: 'gpt-oss-120b (Wafer-scale 30k TPM)',
    desc: 'Ultra-fast inference with dedicated token reservation.',
    docs: 'https://cloud.cerebras.ai',
    badge: 'Fastest',
  },
  gemini: {
    name: 'Google Gemini AI Studio',
    model: 'gemini-2.5-flash / gemini-2.5-pro',
    desc: 'DeepMind multimodal reasoning via Google AI Studio API key.',
    docs: 'https://aistudio.google.com',
    badge: 'Multimodal',
  },
  groq: {
    name: 'Groq Cloud LPU',
    model: 'llama-3.3-70b-versatile (7.2k TPM)',
    desc: 'Low-latency language processing unit with strict token ceilings.',
    docs: 'https://console.groq.com',
    badge: 'LPU Engine',
  },
  'data.gov.in': {
    name: 'Agmarknet Mandi Prices (data.gov.in)',
    model: 'Ministry of Agriculture Mandi Data',
    desc: 'API token for querying live APMC market prices across India.',
    docs: 'https://data.gov.in',
    badge: 'Market Feed',
  },
};

export const CredentialsPage = () => {
  const toast = useToast();

  const [credentials, setCredentials] = useState([]);
  const [isLoading, setIsLoading] = useState(true);

  // New Credential Modal State
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState('cerebras');
  const [secretKey, setSecretKey] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [addError, setAddError] = useState('');

  // Delete Confirmation Modal State
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const loadCredentials = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await credentialService.listCredentials();
      setCredentials(data.items || []);
    } catch (err) {
      toast.error('Failed to load stored BYOK credentials');
    } finally {
      setIsLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    loadCredentials();
  }, [loadCredentials]);

  const handleCreateCredential = async (e) => {
    e.preventDefault();
    if (!secretKey.trim()) {
      setAddError('Please enter the provider API secret key');
      return;
    }

    setAddError('');
    setIsSaving(true);

    try {
      await credentialService.createCredential({
        provider_id: selectedProvider,
        secret: secretKey.trim(),
      });

      // Clear input immediately from memory
      setSecretKey('');
      setIsAddOpen(false);
      toast.success(
        `Enrolled ${selectedProvider} API key with AES-256-GCM encryption`,
        'Credential Stored'
      );
      loadCredentials();
    } catch (err) {
      setAddError(err?.message || 'Failed to enroll credential in vault');
      toast.error(err?.message || 'Enrollment failed');
    } finally {
      setIsSaving(false);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;

    setIsDeleting(true);
    try {
      await credentialService.deleteCredential(deleteTarget.credential_id);
      toast.success(
        `Deleted credential for ${deleteTarget.provider_id}`,
        'Vault Updated'
      );
      setDeleteTarget(null);
      loadCredentials();
    } catch (err) {
      toast.error(err?.message || 'Failed to delete credential');
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-[#E6EAE4]">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Badge variant="active" size="sm">Zero-Knowledge Vault</Badge>
            <span className="text-xs text-[#78837B]">• AES-256-GCM Envelope Encryption</span>
          </div>
          <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
            BYOK Provider Credential Management
          </h1>
          <p className="text-sm text-[#78837B] mt-0.5">
            Store your Cerebras, Gemini, Groq, and data.gov.in API keys securely. Secrets are never stored in plaintext and never logged.
          </p>
        </div>

        <Button
          variant="primary"
          size="md"
          icon={Plus}
          onClick={() => {
            setAddError('');
            setSecretKey('');
            setIsAddOpen(true);
          }}
        >
          Add Provider Key
        </Button>
      </div>

      {/* Security Architecture Callout */}
      <div className="p-5 rounded-2xl bg-[#DCE9DC]/40 border border-[#c2e4cf] flex items-start gap-3.5">
        <ShieldCheck className="w-5 h-5 text-[#183D2B] shrink-0 mt-0.5" />
        <div className="text-xs text-[#183D2B] leading-relaxed">
          <span className="font-bold">Hardware-Backed Security Guarantee: </span>
          Each API key is encrypted with a unique Data Encryption Key (DEK) and wrapped with an OS-level Key Encryption Key (KEK) via Windows Credential Manager. Plaintext keys are never returned across the network and never exposed to other tenant users.
        </div>
      </div>

      {/* Active Enrolled Credentials List */}
      <Card
        title="Enrolled Provider Credentials"
        subtitle="Active encrypted keys available for multi-agent execution"
        headerIcon={KeyRound}
      >
        {isLoading ? (
          <div className="space-y-3">
            <CardSkeleton />
          </div>
        ) : credentials.length === 0 ? (
          <EmptyState
            icon={KeyRound}
            title="No BYOK Credentials Enrolled"
            description="You are currently using the local Deterministic Baseline. To enable Cerebras, Gemini, Groq, or live Mandi prices, add your API key."
            action={
              <Button
                variant="primary"
                size="sm"
                icon={Plus}
                onClick={() => setIsAddOpen(true)}
              >
                Add Your First Key
              </Button>
            }
          />
        ) : (
          <div className="space-y-3">
            {credentials.map((cred) => {
              const meta = PROVIDER_METADATA[cred.provider_id.toLowerCase()] || {
                name: cred.provider_id,
                desc: 'External provider key',
              };

              return (
                <div
                  key={cred.credential_id}
                  className="p-4 rounded-xl border border-[#E6EAE4] bg-[#F7F8F4] flex flex-col sm:flex-row sm:items-center justify-between gap-4 transition-all hover:border-[#347A50]/40"
                >
                  <div className="min-w-0 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-sm text-[#202B24]">
                        {meta.name}
                      </span>
                      <Badge variant="completed" size="sm">
                        {cred.state}
                      </Badge>
                      {meta.badge && (
                        <span className="text-[10px] bg-white border border-[#E6EAE4] text-[#183D2B] font-semibold px-2 py-0.5 rounded-md">
                          {meta.badge}
                        </span>
                      )}
                    </div>

                    <p className="text-xs text-[#78837B] truncate">
                      Credential ID: <code className="bg-white px-1.5 py-0.5 rounded border border-[#E6EAE4] font-mono text-[#202B24]">{cred.credential_id}</code>
                    </p>

                    <div className="flex flex-wrap items-center gap-3 text-[11px] text-[#78837B]">
                      <span>Gen: <strong>{cred.generation}</strong></span>
                      <span>•</span>
                      <span>Updated: <strong>{new Date(cred.updated_at).toLocaleDateString()}</strong></span>
                      <span>•</span>
                      <span className="text-emerald-700 font-medium flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" /> Encrypted at rest
                      </span>
                    </div>
                  </div>

                  <Button
                    variant="outline"
                    size="sm"
                    icon={Trash2}
                    className="text-red-700 hover:bg-red-50 hover:border-red-300 self-end sm:self-center"
                    onClick={() => setDeleteTarget(cred)}
                  >
                    Delete Key
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* Supported Providers Catalog */}
      <div>
        <h3 className="text-sm font-bold text-[#202B24] mb-3">Supported BYOK Ecosystem</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {Object.entries(PROVIDER_METADATA).map(([key, p]) => (
            <Card key={key} padding="p-4" className="flex flex-col justify-between">
              <div>
                <div className="flex items-center justify-between mb-2">
                  <Badge variant="active" size="sm">{key}</Badge>
                  <a
                    href={p.docs}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[#78837B] hover:text-[#183D2B]"
                    title="API Documentation"
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                </div>
                <h4 className="font-semibold text-xs text-[#202B24] mb-1">{p.name}</h4>
                <p className="text-[11px] text-[#78837B] leading-relaxed mb-2">{p.desc}</p>
              </div>
              <div className="pt-2 border-t border-[#E6EAE4] text-[10px] text-[#347A50] font-medium">
                {p.model}
              </div>
            </Card>
          ))}
        </div>
      </div>

      {/* Add Credential Modal */}
      <Modal
        isOpen={isAddOpen}
        onClose={() => setIsAddOpen(false)}
        title="Enroll BYOK Provider Key"
        subtitle="Keys are encrypted with AES-256-GCM and bound to your tenant"
        footer={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsAddOpen(false)}
              disabled={isSaving}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleCreateCredential}
              isLoading={isSaving}
              icon={Lock}
            >
              Encrypt & Store
            </Button>
          </>
        }
      >
        <form onSubmit={handleCreateCredential} className="space-y-4">
          {addError && <Alert type="error">{addError}</Alert>}

          <Select
            label="Inference / Data Provider"
            id="provider_select"
            value={selectedProvider}
            onChange={(e) => setSelectedProvider(e.target.value)}
            options={[
              { value: 'cerebras', label: 'Cerebras Cloud Inference (Recommended)' },
              { value: 'gemini', label: 'Google Gemini AI Studio' },
              { value: 'groq', label: 'Groq Cloud LPU' },
              { value: 'data.gov.in', label: 'data.gov.in Agmarknet Mandi Rates' },
            ]}
            required
          />

          <Input
            label="Provider API Secret Key"
            id="secret_key"
            type="password"
            placeholder="Paste raw API key (e.g. csk-..., AIza..., gsk_...)"
            icon={Lock}
            value={secretKey}
            onChange={(e) => setSecretKey(e.target.value)}
            helperText="The secret is encrypted immediately using Windows Credential Manager KEK and never returned."
            required
            autoFocus
          />
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Confirm Key Deletion"
        subtitle="Are you sure you want to delete this encrypted credential?"
        footer={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDeleteTarget(null)}
              disabled={isDeleting}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={handleDeleteConfirm}
              isLoading={isDeleting}
              icon={Trash2}
            >
              Permanently Delete
            </Button>
          </>
        }
      >
        <p className="text-xs text-[#78837B] leading-relaxed">
          Deleting credential <strong className="text-[#202B24]">{deleteTarget?.credential_id}</strong> for <strong className="text-[#202B24]">{deleteTarget?.provider_id}</strong> will immediately erase the ciphertext from SQLite. Consultations in deterministic mode will continue functioning normally.
        </p>
      </Modal>
    </div>
  );
};
