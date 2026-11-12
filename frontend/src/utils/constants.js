export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api/v1';

export const AGENT_DISPLAY_NAMES = {
  Intake: 'Intake Agent',
  Weather: 'Weather Agent',
  Crop: 'Crop Knowledge Agent',
  Market: 'Market Intelligence Agent',
  Scheme: 'Government Scheme Agent',
  'Risk Alert': 'Risk Alert Agent',
  RiskAlert: 'Risk Alert Agent',
  Coordinator: 'Coordinator Agent',
};

export const AGENT_DESCRIPTIONS = {
  Intake: 'Entity resolution, growth stage mapping, and intent routing.',
  Weather: 'Open-Meteo live meteorology, precipitation, and 3-day forecast.',
  Crop: 'Curated ICAR agronomic guidelines and pest management.',
  Market: 'APMC mandi modal prices and commodity market trends.',
  Scheme: 'Central welfare schemes (PM-KISAN, PMFBY, Rythu Bharosa).',
  'Risk Alert': 'Evidence-backed real-time hazard detection.',
  RiskAlert: 'Evidence-backed real-time hazard detection.',
  Coordinator: 'Multi-perspective synthesis, conflict resolution, and next steps.',
};

export const SEVERITY_COLORS = {
  CRITICAL: {
    bg: 'bg-red-50',
    border: 'border-red-200',
    text: 'text-red-700',
    badge: 'bg-red-100 text-red-800 border-red-200',
    dot: 'bg-red-500',
  },
  HIGH: {
    bg: 'bg-amber-50',
    border: 'border-amber-200',
    text: 'text-amber-800',
    badge: 'bg-amber-100 text-amber-800 border-amber-200',
    dot: 'bg-amber-500',
  },
  MEDIUM: {
    bg: 'bg-yellow-50',
    border: 'border-yellow-200',
    text: 'text-yellow-800',
    badge: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    dot: 'bg-yellow-500',
  },
  LOW: {
    bg: 'bg-emerald-50',
    border: 'border-emerald-200',
    text: 'text-emerald-800',
    badge: 'bg-emerald-100 text-emerald-800 border-emerald-200',
    dot: 'bg-emerald-500',
  },
};
