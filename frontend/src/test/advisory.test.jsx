import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { AgentExecutionViewer } from '../components/advisory/AgentExecutionViewer';
import { CoordinatorSynthesisPanel } from '../components/advisory/CoordinatorSynthesisPanel';
import { RiskAlertsPanel } from '../components/advisory/RiskAlertsPanel';
import { WeatherEvidencePanel } from '../components/advisory/WeatherEvidencePanel';
import { MarketEvidencePanel } from '../components/advisory/MarketEvidencePanel';
import { SchemeEvidencePanel } from '../components/advisory/SchemeEvidencePanel';

describe('Advisory & Agent Intelligence Presentation Tests', () => {
  it('1. Seven-Agent Execution Viewer renders all 7 agents and statuses', () => {
    const mockAgentResults = [
      { agent_id: 'Intake', status: 'COMPLETED', duration_ms: 15.2, summary: 'Resolved cotton' },
      { agent_id: 'Weather', status: 'COMPLETED', duration_ms: 210.0, summary: 'Open-Meteo ok' },
      { agent_id: 'Crop', status: 'UNAVAILABLE', duration_ms: 45.0, error: 'No qualifying crop bulletin' },
      { agent_id: 'Market', status: 'PARTIAL', duration_ms: 120.0, summary: 'Single-day observation' },
      { agent_id: 'Scheme', status: 'COMPLETED', duration_ms: 80.0, summary: 'PM-KISAN match' },
      { agent_id: 'Risk Alert', status: 'COMPLETED', duration_ms: 55.0, summary: '1 active risk' },
      { agent_id: 'Coordinator', status: 'COMPLETED', duration_ms: 90.0, summary: 'Full synthesis done' },
    ];

    render(
      <AgentExecutionViewer
        agentResults={mockAgentResults}
        boardState="SYNTHESIZED"
      />
    );

    expect(screen.getByText(/Seven-Agent Workflow Execution/i)).toBeInTheDocument();
    expect(screen.getByText(/Intake Agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Weather Agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Crop Knowledge Agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Market Intelligence Agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Government Scheme Agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Risk Alert Agent/i)).toBeInTheDocument();
    expect(screen.getByText(/Coordinator Agent/i)).toBeInTheDocument();

    // Verify status chips
    expect(screen.getByText(/Unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/Partial/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Completed/i).length).toBeGreaterThan(0);
  });

  it('2. Coordinator Synthesis renders recommendations and interactive steps', () => {
    const nextSteps = [
      'Scout for bollworm larvae under leaves',
      'Check Adilabad APMC mandi prices before selling',
    ];
    const limitations = ['Adilabad APMC market data was unavailable for today'];

    render(
      <CoordinatorSynthesisPanel
        synthesis="### Executive Summary\nApply neem spray for early protection."
        suggestedNextSteps={nextSteps}
        uncertaintyAndLimitations={limitations}
        status="COMPLETED"
        providerId="cerebras"
        modelId="gpt-oss-120b"
      />
    );

    expect(screen.getByText(/Coordinator Synthesis & Advisory/i)).toBeInTheDocument();
    expect(screen.getByText(/Apply neem spray for early protection/i)).toBeInTheDocument();
    expect(screen.getByText(/Scout for bollworm larvae under leaves/i)).toBeInTheDocument();
    expect(screen.getByText(/Check Adilabad APMC mandi prices before selling/i)).toBeInTheDocument();
    expect(screen.getByText(/Adilabad APMC market data was unavailable for today/i)).toBeInTheDocument();
    expect(screen.getByText(/cerebras/i)).toBeInTheDocument();
  });

  it('3. Risk Alerts Panel clearly separates active hazards from background vulnerabilities', () => {
    const activeAlerts = [
      {
        finding_id: 'f-1',
        title: 'Excess Rainfall Warning',
        severity: 'HIGH',
        explanation: 'Forecast indicates >50mm rain within 48 hours.',
        safe_action: 'Ensure field drainage channels are cleared.',
        evidence_ids: ['ev:open-meteo:1'],
      },
    ];

    const backgroundVulns = [
      {
        finding_id: 'f-2',
        title: 'General Bollworm Susceptibility',
        severity: 'LOW',
        explanation: 'Cotton is agronomically vulnerable to bollworm at flowering.',
        safe_action: 'Install pheromone monitoring traps.',
      },
    ];

    render(
      <RiskAlertsPanel
        activeAlerts={activeAlerts}
        backgroundVulnerabilities={backgroundVulns}
      />
    );

    // Active hazard displayed prominently
    expect(screen.getByText(/Active Agricultural Risk Alerts/i)).toBeInTheDocument();
    expect(screen.getByText(/Excess Rainfall Warning/i)).toBeInTheDocument();
    expect(screen.getByText(/Forecast indicates >50mm rain within 48 hours/i)).toBeInTheDocument();
    expect(screen.getByText(/Ensure field drainage channels are cleared/i)).toBeInTheDocument();

    // Background vulnerability in collapsible section
    expect(screen.getByText(/General Crop Susceptibility & Background Vulnerabilities/i)).toBeInTheDocument();

    // Expand background section
    fireEvent.click(screen.getByText(/General Crop Susceptibility & Background Vulnerabilities/i));
    expect(screen.getByText(/General Bollworm Susceptibility/i)).toBeInTheDocument();
    expect(screen.getByText(/Install pheromone monitoring traps/i)).toBeInTheDocument();
  });

  it('4. Weather Evidence Panel displays Open-Meteo observations with provenance', () => {
    const weather = [
      {
        evidence_id: 'ev-weather-1',
        title: 'Open-Meteo Weather Observation',
        excerpt: 'Temperature: 28.5C, Relative humidity: 72%, Wind: 14 km/h',
        provenance: 'Open-Meteo REST API',
        reference_url: 'https://open-meteo.com',
      },
    ];

    render(<WeatherEvidencePanel weatherEvidence={weather} />);

    expect(screen.getByText(/Meteorological Evidence & Observations/i)).toBeInTheDocument();
    expect(screen.getByText(/Temperature: 28.5C/i)).toBeInTheDocument();
    expect(screen.getByText(/Open-Meteo REST API/i)).toBeInTheDocument();
  });

  it('5. Market Evidence Panel renders APMC mandi prices and trade caution', () => {
    const market = [
      {
        evidence_id: 'ev-market-1',
        title: 'Adilabad APMC Cotton Mandi Trade',
        excerpt: 'Modal price: ₹8,870/quintal (Min: ₹8,231, Max: ₹8,870)',
        provenance: 'Agmarknet via data.gov.in',
      },
    ];

    render(<MarketEvidencePanel marketEvidence={market} />);

    expect(screen.getByText(/Mandi Commodity Market Evidence/i)).toBeInTheDocument();
    expect(screen.getByText(/Modal price: ₹8,870\/quintal/i)).toBeInTheDocument();
  });

  it('6. Scheme Evidence Panel renders government scheme support', () => {
    const schemes = [
      {
        evidence_id: 'ev-scheme-1',
        title: 'PMFBY Pradhan Mantri Fasal Bima Yojana',
        excerpt: 'Comprehensive crop insurance covering non-preventable natural risks.',
        provenance: 'Ministry of Agriculture and Farmers Welfare',
        reference_url: 'https://pmfby.gov.in',
      },
    ];

    render(<SchemeEvidencePanel schemeEvidence={schemes} />);

    expect(screen.getByText(/Government Agricultural Schemes/i)).toBeInTheDocument();
    expect(screen.getByText(/PMFBY Pradhan Mantri Fasal Bima Yojana/i)).toBeInTheDocument();
    expect(screen.getByText(/Comprehensive crop insurance covering non-preventable natural risks/i)).toBeInTheDocument();
  });
});
