import React, { useState, useEffect } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import {
  Sparkles,
  Sprout,
  MapPin,
  Calendar,
  Layers,
  Cpu,
  KeyRound,
  AlertCircle,
  HelpCircle,
  CheckCircle2,
  Clock,
  ArrowRight,
} from 'lucide-react';
import { useConfig } from '../../context/ConfigContext';
import { useToast } from '../../context/ToastContext';
import { advisoryService } from '../../services/advisoryService';
import { credentialService } from '../../services/credentialService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Input, Select, Textarea } from '../../components/ui/Input';
import { Badge } from '../../components/ui/Badge';
import { Alert } from '../../components/ui/Feedback';

export const NewAdvisoryPage = () => {
  const { crops, locations, providers, isLoading: configLoading } = useConfig();
  const toast = useToast();
  const navigate = useNavigate();

  // Form State
  const [query, setQuery] = useState('');
  const [selectedCrop, setSelectedCrop] = useState('');
  const [selectedLocation, setSelectedLocation] = useState('');
  const [selectedStage, setSelectedStage] = useState('');
  const [selectedVariety, setSelectedVariety] = useState('');
  const [selectedMode, setSelectedMode] = useState('deterministic');
  const [selectedCredentialId, setSelectedCredentialId] = useState('');

  // Credentials State
  const [credentials, setCredentials] = useState([]);
  const [credsLoading, setCredsLoading] = useState(false);

  // Submission State
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitStep, setSubmitStep] = useState(0);
  const [error, setError] = useState('');

  // Sample prompt chips
  const samplePrompts = [
    {
      label: 'Flowering Cotton Pests',
      query: 'My cotton crop is flowering in Adilabad. What pests should I monitor and what are current mandi prices?',
      crop: 'crop:cotton',
      location: 'loc:adilabad',
      stage: 'flowering',
    },
    {
      label: 'Rice Tillering Disease',
      query: 'Rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?',
      crop: 'crop:rice',
      location: 'loc:punjab',
      stage: 'tillering',
    },
    {
      label: 'APMC Market Trends',
      query: 'What are the current modal prices and trading range for cotton in Adilabad APMC?',
      crop: 'crop:cotton',
      location: 'loc:adilabad',
      stage: 'harvesting',
    },
  ];

  // Fetch BYOK credentials when external LLM mode is chosen
  useEffect(() => {
    if (selectedMode !== 'deterministic') {
      setCredsLoading(true);
      credentialService
        .listCredentials()
        .then((data) => {
          const matching = (data.items || []).filter(
            (c) => c.provider_id.toLowerCase() === selectedMode.toLowerCase() && c.state === 'active'
          );
          setCredentials(matching);
          if (matching.length > 0) {
            setSelectedCredentialId(matching[0].credential_id);
          } else {
            setSelectedCredentialId('');
          }
        })
        .catch(() => setCredentials([]))
        .finally(() => setCredsLoading(false));
    } else {
      setSelectedCredentialId('');
    }
  }, [selectedMode]);

  // Derived growth stages & varieties for selected crop
  const currentCropObj = crops.find((c) => c.crop_id === selectedCrop);
  const availableStages = currentCropObj?.growth_stages || [];
  const availableVarieties = currentCropObj?.varieties || [];

  const handleApplySample = (sample) => {
    setQuery(sample.query);
    if (sample.crop) setSelectedCrop(sample.crop);
    if (sample.location) setSelectedLocation(sample.location);
    if (sample.stage) setSelectedStage(sample.stage);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!query.trim() || query.trim().length < 3) {
      setError('Please provide a specific agronomic inquiry (at least 3 characters)');
      return;
    }

    if (selectedMode !== 'deterministic' && !selectedCredentialId && credentials.length === 0) {
      setError(`Active BYOK credential required for ${selectedMode}. Please enroll a key in the BYOK vault.`);
      return;
    }

    setError('');
    setIsSubmitting(true);
    setSubmitStep(1);

    // Simulated progress steps for the 7-agent workflow visualization while waiting
    const stepTimer = setInterval(() => {
      setSubmitStep((prev) => (prev < 6 ? prev + 1 : prev));
    }, 1800);

    try {
      const cropName = currentCropObj?.common_names?.[0] || selectedCrop;
      const locObj = locations.find((l) => l.location_id === selectedLocation);
      const locName = locObj?.display_name || selectedLocation;

      const advisory = await advisoryService.createAdvisory({
        query: query.trim(),
        crop: cropName || undefined,
        location: locName || undefined,
        growth_stage: selectedStage || undefined,
        variety: selectedVariety || undefined,
        mode: selectedMode,
        credential_id: selectedCredentialId || undefined,
      });

      clearInterval(stepTimer);
      toast.success('Seven-agent consultation completed!', 'Advisory Ready');
      navigate(`/advisories/${encodeURIComponent(advisory.advisory_id)}`);
    } catch (err) {
      clearInterval(stepTimer);
      setIsSubmitting(false);
      setError(err?.message || 'Seven-agent advisory workflow failed. Check parameters and try again.');
      toast.error(err?.message || 'Workflow execution error');
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Badge variant="active" size="sm">Guided Consultation</Badge>
          <span className="text-xs text-[#78837B]">• 7-Agent Orchestrated Workflow</span>
        </div>
        <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
          New Agricultural Advisory Inquiry
        </h1>
        <p className="text-sm text-[#78837B] mt-1">
          Submit your field query. The Intake, Weather, Crop, Market, Scheme, and Risk Alert agents will analyze your context before the Coordinator synthesizes recommendations.
        </p>
      </div>

      {error && (
        <Alert type="error" title="Submission Error">
          {error}
        </Alert>
      )}

      {/* Suggested Prompt Chips */}
      <div className="space-y-2">
        <span className="text-xs font-semibold text-[#78837B]">Sample Inquiries:</span>
        <div className="flex flex-wrap gap-2">
          {samplePrompts.map((s, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => handleApplySample(s)}
              className="text-xs px-3 py-1.5 rounded-xl border border-[#E6EAE4] bg-white hover:border-[#347A50] hover:bg-[#F7F8F4] text-[#202B24] transition-colors flex items-center gap-1.5"
            >
              <Sparkles className="w-3.5 h-3.5 text-[#347A50]" />
              <span>{s.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Form Card */}
      <Card padding="p-6 sm:p-8">
        <form onSubmit={handleSubmit} className="space-y-6">
          {/* 1. Farmer Question */}
          <div>
            <Textarea
              label="Agronomic Question / Observation"
              id="query"
              rows={4}
              placeholder="Describe your field observations, symptoms, weather concerns, or market pricing questions..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              helperText="Be specific about leaf coloration, pest sightings, or weather patterns for precise grounding."
              required
            />
          </div>

          {/* 2. Crop & Location Context (Grouped) */}
          <div className="pt-4 border-t border-[#E6EAE4]">
            <h3 className="text-sm font-bold text-[#202B24] mb-4 flex items-center gap-2">
              <Sprout className="w-4 h-4 text-[#347A50]" />
              <span>Crop & Geographic Context</span>
            </h3>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Select
                label="Crop"
                id="crop"
                icon={Sprout}
                value={selectedCrop}
                onChange={(e) => {
                  setSelectedCrop(e.target.value);
                  setSelectedStage('');
                  setSelectedVariety('');
                }}
                placeholder="Select Crop"
                options={crops.map((c) => ({
                  value: c.crop_id,
                  label: `${c.common_names?.[0]} (${c.scientific_name})`,
                }))}
              />

              <Select
                label="Location / District"
                id="location"
                icon={MapPin}
                value={selectedLocation}
                onChange={(e) => setSelectedLocation(e.target.value)}
                placeholder="Select District / Region"
                options={locations.map((l) => ({
                  value: l.location_id,
                  label: l.display_name,
                }))}
              />

              <Select
                label="Growth Stage"
                id="growth_stage"
                icon={Layers}
                value={selectedStage}
                onChange={(e) => setSelectedStage(e.target.value)}
                placeholder={availableStages.length > 0 ? 'Select Growth Stage' : 'Select a crop first'}
                disabled={availableStages.length === 0}
                options={availableStages.map((s) => ({
                  value: s.stage_id,
                  label: s.names?.[0] || s.stage_id,
                }))}
              />

              <Select
                label="Crop Variety"
                id="variety"
                icon={Calendar}
                value={selectedVariety}
                onChange={(e) => setSelectedVariety(e.target.value)}
                placeholder={availableVarieties.length > 0 ? 'Select Variety' : 'Select a crop first'}
                disabled={availableVarieties.length === 0}
                options={availableVarieties.map((v) => ({
                  value: v.variety_id,
                  label: v.names?.[0] || v.variety_id,
                }))}
              />
            </div>
          </div>

          {/* 3. Inference Engine & BYOK Provider Selection */}
          <div className="pt-4 border-t border-[#E6EAE4]">
            <h3 className="text-sm font-bold text-[#202B24] mb-4 flex items-center gap-2">
              <Cpu className="w-4 h-4 text-[#347A50]" />
              <span>Inference Engine & Security Mode</span>
            </h3>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <Select
                label="Execution Mode"
                id="mode"
                value={selectedMode}
                onChange={(e) => setSelectedMode(e.target.value)}
                placeholder=""
                options={[
                  { value: 'deterministic', label: 'Deterministic Baseline (Local, Zero-API-Cost)' },
                  { value: 'cerebras', label: 'Cerebras Cloud WSE-3 (30,000 TPM limit)' },
                  { value: 'gemini', label: 'Google Gemini 2.5 Flash / Pro (BYOK)' },
                  { value: 'groq', label: 'Groq Cloud LPU (7,200 TPM limit)' },
                ]}
              />

              {selectedMode !== 'deterministic' && (
                <div>
                  <Select
                    label="Stored BYOK Credential"
                    id="credential_id"
                    icon={KeyRound}
                    value={selectedCredentialId}
                    onChange={(e) => setSelectedCredentialId(e.target.value)}
                    placeholder={
                      credsLoading
                        ? 'Loading active credentials...'
                        : credentials.length > 0
                        ? 'Select Active Credential'
                        : 'No active key found'
                    }
                    options={credentials.map((c) => ({
                      value: c.credential_id,
                      label: `${c.credential_id.slice(0, 14)}... (Gen ${c.generation})`,
                    }))}
                  />

                  {credentials.length === 0 && !credsLoading && (
                    <p className="mt-1 text-xs text-amber-700">
                      No active key for {selectedMode}.{' '}
                      <Link to="/credentials" className="underline font-semibold">
                        Add key in BYOK Vault
                      </Link>
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Submit Action */}
          <div className="pt-4 border-t border-[#E6EAE4] flex flex-col sm:flex-row items-center justify-between gap-4">
            <span className="text-xs text-[#78837B]">
              Responses are verified by 7 specialized agents with zero-knowledge encryption.
            </span>

            <Button
              type="submit"
              variant="primary"
              size="lg"
              isLoading={isSubmitting}
              icon={Sparkles}
              className="w-full sm:w-auto"
            >
              Execute 7-Agent Consultation
            </Button>
          </div>
        </form>
      </Card>

      {/* Real-time Workflow Execution Progress Overlay */}
      {isSubmitting && (
        <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl border border-[#E6EAE4] p-6 sm:p-8 max-w-lg w-full shadow-2xl space-y-6 animate-in zoom-in-95">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-[#183D2B] text-white flex items-center justify-center animate-pulse">
                <Sparkles className="w-5 h-5 text-[#DCE9DC]" />
              </div>
              <div>
                <h3 className="font-bold text-lg text-[#202B24]">
                  Executing Seven-Agent Advisory
                </h3>
                <p className="text-xs text-[#78837B]">
                  Microsoft Agent Framework graph reasoning in progress...
                </p>
              </div>
            </div>

            <div className="space-y-2.5">
              {[
                '1. Intake Agent: Normalizing agricultural query and entities...',
                '2. Weather Agent: Fetching Open-Meteo meteorological feed...',
                '3. Crop Knowledge Agent: Retrieving curated ICAR advisories...',
                '4. Market Agent: Cross-referencing APMC mandi commodity trade...',
                '5. Scheme Agent: Checking central and state welfare benefits...',
                '6. Risk Alert Agent: Screening for active weather & pest hazards...',
                '7. Coordinator Agent: Synthesizing multi-agent consensus...',
              ].map((stepText, idx) => {
                const isPassed = submitStep > idx;
                const isCurrent = submitStep === idx;

                return (
                  <div
                    key={idx}
                    className={`flex items-center gap-3 p-2.5 rounded-xl text-xs transition-all ${
                      isCurrent
                        ? 'bg-[#DCE9DC] text-[#183D2B] font-semibold border border-[#c2e4cf]'
                        : isPassed
                        ? 'bg-emerald-50 text-emerald-800'
                        : 'text-slate-400'
                    }`}
                  >
                    {isPassed ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                    ) : isCurrent ? (
                      <Clock className="w-4 h-4 text-[#347A50] animate-spin shrink-0" />
                    ) : (
                      <span className="w-4 h-4 rounded-full border border-slate-300 shrink-0" />
                    )}
                    <span className="truncate">{stepText}</span>
                  </div>
                );
              })}
            </div>

            <p className="text-center text-[11px] text-[#78837B]">
              Please wait while the advisory board evaluates evidence without hallucination.
            </p>
          </div>
        </div>
      )}
    </div>
  );
};
