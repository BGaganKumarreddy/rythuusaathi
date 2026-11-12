import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Sparkles,
  Sprout,
  MapPin,
  Layers,
  Calendar,
  Send,
  HelpCircle,
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  AlertCircle,
  FileQuestion,
  Compass,
} from 'lucide-react';
import { useConfig } from '../../context/ConfigContext';
import { useToast } from '../../context/ToastContext';
import { advisoryService } from '../../services/advisoryService';
import { Card } from '../../components/ui/Card';
import { Button } from '../../components/ui/Button';
import { Input, Select, Textarea } from '../../components/ui/Input';
import { Badge } from '../../components/ui/Badge';
import { Alert } from '../../components/ui/Feedback';

export const NewAdvisoryPage = () => {
  const { crops, locations } = useConfig();
  const toast = useToast();
  const navigate = useNavigate();

  // Natural language inquiry (Primary farmer input)
  const [query, setQuery] = useState('');

  // Optional contextual helpers
  const [showOptionalDetails, setShowOptionalDetails] = useState(false);
  const [selectedCrop, setSelectedCrop] = useState('');
  const [customCrop, setCustomCrop] = useState('');
  const [selectedState, setSelectedState] = useState('');
  const [selectedDistrict, setSelectedDistrict] = useState('');
  const [customLocation, setCustomLocation] = useState('');
  const [selectedStage, setSelectedStage] = useState('');
  const [selectedVariety, setSelectedVariety] = useState('');
  const [customVariety, setCustomVariety] = useState('');

  // Submission state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitStep, setSubmitStep] = useState(0);
  const [error, setError] = useState('');

  // Plain-language sample prompts for farmers
  const samplePrompts = [
    {
      label: 'Rice Brown Leaf Spots',
      query: 'Rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?',
      description: 'Tillering rice with fungal symptom observations',
    },
    {
      label: 'Wheat Yellowing Leaves',
      query: 'I am growing wheat. Some leaves are turning yellow.',
      description: 'Nutrient or moisture stress inquiry',
    },
    {
      label: 'Punjab Govt Schemes',
      query: 'My field is in Ludhiana, Punjab. What government schemes can I apply for?',
      description: 'Jurisdiction-specific subsidy and insurance query',
    },
    {
      label: 'Adilabad Cotton Mandi Price',
      query: 'What is the current market price for cotton in Adilabad APMC?',
      description: 'Daily modal price and trade range inquiry',
    },
  ];

  // Derived location scopes (never mix country with districts)
  const states = locations.filter((l) => l.level === 'state');
  const availableDistricts = locations.filter(
    (l) => l.level === 'district' && (!selectedState || l.parent_id === selectedState)
  );

  // Derived growth stages & varieties for selected crop
  const currentCropObj = crops.find((c) => c.crop_id === selectedCrop);
  const availableStages = currentCropObj?.growth_stages || [];
  const availableVarieties = currentCropObj?.varieties || [];

  const handleApplySample = (sample) => {
    setQuery(sample.query);
    setError('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!query.trim() || query.trim().length < 3) {
      setError('Please describe your farming question or observation in plain words (at least 3 characters).');
      return;
    }

    setError('');
    setIsSubmitting(true);
    setSubmitStep(1);

    const stepTimer = setInterval(() => {
      setSubmitStep((prev) => (prev < 6 ? prev + 1 : prev));
    }, 1200);

    try {
      // Determine crop value
      let finalCrop = undefined;
      if (selectedCrop === '__other__') {
        finalCrop = customCrop.trim() || undefined;
      } else if (selectedCrop) {
        finalCrop = currentCropObj?.common_names?.[0] || selectedCrop;
      }

      // Determine location value (hierarchical resolution)
      let finalLocation = undefined;
      if (selectedDistrict === '__other__' || selectedState === '__other__') {
        finalLocation = customLocation.trim() || undefined;
      } else if (selectedDistrict) {
        const distObj = locations.find((l) => l.location_id === selectedDistrict);
        finalLocation = distObj?.display_name || selectedDistrict;
      } else if (selectedState) {
        const stateObj = locations.find((l) => l.location_id === selectedState);
        finalLocation = stateObj?.display_name || selectedState;
      }

      // Determine variety value
      let finalVariety = undefined;
      if (selectedVariety === '__other__') {
        finalVariety = customVariety.trim() || undefined;
      } else if (selectedVariety) {
        const varObj = availableVarieties.find((v) => v.variety_id === selectedVariety);
        finalVariety = varObj?.names?.[0] || selectedVariety;
      }

      // Determine growth stage
      const finalStage = selectedStage || undefined;

      const advisory = await advisoryService.createAdvisory({
        query: query.trim(),
        crop: finalCrop,
        location: finalLocation,
        growth_stage: finalStage,
        variety: finalVariety,
        mode: 'deterministic',
      });

      clearInterval(stepTimer);
      toast.success('Consultation completed successfully!', 'Advisory Ready');
      navigate(`/advisories/${encodeURIComponent(advisory.advisory_id)}`);
    } catch (err) {
      clearInterval(stepTimer);
      setIsSubmitting(false);
      setError(err?.message || 'Unable to complete advisory workflow. Please verify details and try again.');
      toast.error(err?.message || 'Workflow execution error');
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <Badge variant="active" size="sm">Farmer Consultation</Badge>
          <span className="text-xs text-[#78837B]">• Natural-Language Advisory</span>
        </div>
        <h1 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
          Ask Your Farming Question
        </h1>
        <p className="text-sm text-[#78837B] mt-1">
          Describe what you see in your field, ask about crop symptoms, weather, mandi prices, or government schemes in ordinary language.
        </p>
      </div>

      {/* Quick Example Chips */}
      <div>
        <p className="text-xs font-bold text-[#78837B] uppercase tracking-wider mb-2 flex items-center gap-1.5">
          <FileQuestion className="w-3.5 h-3.5 text-[#347A50]" />
          <span>Common Example Questions (Click to Fill)</span>
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {samplePrompts.map((p, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => handleApplySample(p)}
              className="text-left p-3 rounded-xl bg-white border border-[#E6EAE4] hover:border-[#347A50] hover:bg-[#F7F8F4] transition-all group"
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-xs text-[#202B24] group-hover:text-[#183D2B]">
                  {p.label}
                </span>
                <span className="text-[10px] text-[#347A50] font-bold group-hover:translate-x-0.5 transition-transform">
                  Use &rarr;
                </span>
              </div>
              <p className="text-xs text-[#78837B] line-clamp-1 mt-1">{p.query}</p>
            </button>
          ))}
        </div>
      </div>

      {error && <Alert type="error" title="Consultation Notice">{error}</Alert>}

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Large Natural Language Text Area */}
        <Card>
          <div className="space-y-3">
            <Textarea
              label="Describe your problem or question"
              id="query"
              rows={5}
              placeholder="e.g. Rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available? (Or describe your location, mandi pricing questions, or crop symptoms...)"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              helperText="You can mention your crop, growth stage, location, or symptoms directly in your question. Our Intake agent will automatically extract them."
              required
            />
          </div>
        </Card>

        {/* Collapsible Optional Field Details Accordion */}
        <div className="rounded-2xl border border-[#E6EAE4] bg-white overflow-hidden shadow-xs">
          <button
            type="button"
            onClick={() => setShowOptionalDetails((prev) => !prev)}
            className="w-full px-5 py-4 flex items-center justify-between text-left hover:bg-[#F7F8F4] transition-colors"
          >
            <div className="flex items-center gap-2.5">
              <Compass className="w-4 h-4 text-[#347A50]" />
              <div>
                <span className="text-sm font-bold text-[#202B24]">
                  Specify or Correct Field Details
                </span>
                <span className="text-xs text-[#78837B] ml-2 font-normal">
                  (Optional — Leave blank to let the system extract from your question)
                </span>
              </div>
            </div>
            {showOptionalDetails ? (
              <ChevronUp className="w-4 h-4 text-[#78837B]" />
            ) : (
              <ChevronDown className="w-4 h-4 text-[#78837B]" />
            )}
          </button>

          {showOptionalDetails && (
            <div className="px-5 pb-6 pt-2 border-t border-[#E6EAE4] space-y-5 bg-[#FDFEFC]">
              {/* Crop & Custom Crop */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <Select
                    label="Crop (Optional)"
                    id="crop"
                    icon={Sprout}
                    value={selectedCrop}
                    onChange={(e) => {
                      setSelectedCrop(e.target.value);
                      setSelectedStage('');
                      setSelectedVariety('');
                    }}
                    placeholder="Not specified (Auto-extract)"
                    options={[
                      ...crops.map((c) => ({
                        value: c.crop_id,
                        label: `${c.common_names?.[0]} (${c.scientific_name})`,
                      })),
                      { value: '__other__', label: '+ Other / Not listed crop' },
                    ]}
                  />
                  {selectedCrop === '__other__' && (
                    <div className="mt-2">
                      <Input
                        label="Enter Unlisted Crop Name"
                        id="customCrop"
                        placeholder="e.g. Mustard, Sugarcane, Sorghum"
                        value={customCrop}
                        onChange={(e) => setCustomCrop(e.target.value)}
                        helperText="Unregistered crops are accepted safely for general advisory"
                      />
                    </div>
                  )}
                </div>

                {/* Growth Stage */}
                <div>
                  <Select
                    label="Growth Stage (Optional)"
                    id="growth_stage"
                    icon={Layers}
                    value={selectedStage}
                    onChange={(e) => setSelectedStage(e.target.value)}
                    placeholder="Not specified (Optional)"
                    options={
                      availableStages.length > 0
                        ? availableStages.map((s) => ({
                            value: s.stage_id,
                            label: s.names?.[0] || s.stage_id,
                          }))
                        : [
                            { value: 'seedling', label: 'Seedling / Early vegetative' },
                            { value: 'vegetative', label: 'Vegetative / Tillering' },
                            { value: 'flowering', label: 'Flowering / Bloom' },
                            { value: 'grain-filling', label: 'Grain Filling / Pod Formation' },
                            { value: 'maturity', label: 'Maturity / Harvesting' },
                          ]
                    }
                  />
                </div>
              </div>

              {/* Hierarchical Location: State -> District */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2 border-t border-[#E6EAE4]">
                <div>
                  <Select
                    label="State (Optional)"
                    id="state"
                    icon={MapPin}
                    value={selectedState}
                    onChange={(e) => {
                      setSelectedState(e.target.value);
                      setSelectedDistrict('');
                    }}
                    placeholder="Select State"
                    options={[
                      ...states.map((s) => ({
                        value: s.location_id,
                        label: s.display_name,
                      })),
                      { value: '__other__', label: '+ Other State' },
                    ]}
                  />
                </div>

                <div>
                  <Select
                    label="District (Optional)"
                    id="district"
                    icon={MapPin}
                    value={selectedDistrict}
                    onChange={(e) => setSelectedDistrict(e.target.value)}
                    placeholder={
                      selectedState === '__other__'
                        ? 'Select Other'
                        : availableDistricts.length > 0
                        ? 'Select District'
                        : 'Select state first'
                    }
                    disabled={!selectedState}
                    options={[
                      ...availableDistricts.map((d) => ({
                        value: d.location_id,
                        label: d.display_name,
                      })),
                      { value: '__other__', label: '+ Other District / Village' },
                    ]}
                  />
                </div>
              </div>

              {(selectedState === '__other__' || selectedDistrict === '__other__') && (
                <div>
                  <Input
                    label="Enter Custom Location / District / Village"
                    id="customLocation"
                    placeholder="e.g. Warangal, Telangana or Ludhiana rural"
                    value={customLocation}
                    onChange={(e) => setCustomLocation(e.target.value)}
                    helperText="Custom locations will be evaluated against available state schemes and regional data"
                  />
                </div>
              )}

              {/* Crop Variety */}
              <div className="pt-2 border-t border-[#E6EAE4]">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <Select
                      label="Crop Variety (Optional)"
                      id="variety"
                      icon={Calendar}
                      value={selectedVariety}
                      onChange={(e) => setSelectedVariety(e.target.value)}
                      placeholder="Not specified (Optional)"
                      options={[
                        ...availableVarieties.map((v) => ({
                          value: v.variety_id,
                          label: v.names?.[0] || v.variety_id,
                        })),
                        { value: '__other__', label: '+ Other / Custom Variety' },
                      ]}
                    />
                    <p className="mt-1 text-[11px] text-[#78837B]">
                      Showing registered ICAR starter varieties. You can also specify a custom variety.
                    </p>
                  </div>

                  {selectedVariety === '__other__' && (
                    <div>
                      <Input
                        label="Enter Custom Variety Name"
                        id="customVariety"
                        placeholder="e.g. Sona Masoori, HMT, Local Desi"
                        value={customVariety}
                        onChange={(e) => setCustomVariety(e.target.value)}
                      />
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Submit Action Card */}
        <Card>
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
            <div className="text-xs text-[#78837B] leading-relaxed">
              <span className="font-semibold text-[#202B24]">Privacy & Evidence Grounding:</span>{' '}
              All recommendations are grounded against official ICAR advisories, Open-Meteo observations, and Agmarknet mandi records.
            </div>

            <Button
              type="submit"
              variant="primary"
              size="lg"
              disabled={isSubmitting || !query.trim()}
              icon={Send}
            >
              {isSubmitting ? 'Analyzing with 7 Agents...' : 'Get Agricultural Advice'}
            </Button>
          </div>
        </Card>
      </form>

      {/* Seven-Agent Execution Overlay */}
      {isSubmitting && (
        <div className="fixed inset-0 z-50 bg-[#183D2B]/70 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-3xl p-6 sm:p-8 max-w-md w-full shadow-2xl border border-[#E6EAE4] space-y-6 animate-in zoom-in-95 duration-200">
            <div className="text-center space-y-2">
              <div className="w-14 h-14 rounded-2xl bg-[#DCE9DC] text-[#183D2B] mx-auto flex items-center justify-center">
                <Sprout className="w-8 h-8 animate-pulse text-[#347A50]" />
              </div>
              <h3 className="text-lg font-bold text-[#202B24]">
                Orchestrating 7 Agricultural Agents
              </h3>
              <p className="text-xs text-[#78837B]">
                Synthesizing weather forecasts, agronomy advisories, and mandi prices...
              </p>
            </div>

            <div className="space-y-3">
              {[
                { name: 'Intake Agent', label: 'Extracting crop, stage, and symptoms from query' },
                { name: 'Weather Agent', label: 'Retrieving Open-Meteo agrometeorological data' },
                { name: 'Crop Health Agent', label: 'Matching ICAR agronomic research guidelines' },
                { name: 'Mandi Market Agent', label: 'Scanning Agmarknet modal APMC rates' },
                { name: 'Government Scheme Agent', label: 'Evaluating PMFBY and state eligibility' },
                { name: 'Risk Alert Agent', label: 'Screening active weather & pest thresholds' },
                { name: 'Coordinator Agent', label: 'Synthesizing evidence into farmer next steps' },
              ].map((step, idx) => {
                const isDone = submitStep > idx;
                const isCurrent = submitStep === idx;
                return (
                  <div key={idx} className="flex items-center gap-3 text-xs">
                    <div
                      className={`w-6 h-6 rounded-full flex items-center justify-center font-bold text-[10px] shrink-0 transition-colors ${
                        isDone
                          ? 'bg-[#347A50] text-white'
                          : isCurrent
                          ? 'bg-amber-100 text-amber-800 border-2 border-amber-500 animate-spin'
                          : 'bg-[#F7F8F4] text-[#78837B] border border-[#E6EAE4]'
                      }`}
                    >
                      {isDone ? <CheckCircle2 className="w-3.5 h-3.5" /> : idx + 1}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p
                        className={`font-semibold ${
                          isDone || isCurrent ? 'text-[#202B24]' : 'text-[#78837B]'
                        }`}
                      >
                        {step.name}
                      </p>
                      <p className="text-[11px] text-[#78837B] truncate">{step.label}</p>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
