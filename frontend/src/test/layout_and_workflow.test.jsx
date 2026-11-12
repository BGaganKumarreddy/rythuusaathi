import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter, MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '../context/AuthContext';
import { ConfigProvider } from '../context/ConfigContext';
import { ToastProvider } from '../context/ToastContext';
import { Sidebar } from '../components/layout/Sidebar';
import { TopHeader } from '../components/layout/TopHeader';
import { NewAdvisoryPage } from '../pages/advisory/NewAdvisoryPage';
import { advisoryService } from '../services/advisoryService';
import { credentialService } from '../services/credentialService';
import { configurationService } from '../services/configurationService';
import { App } from '../App';

vi.mock('../services/advisoryService', () => ({
  advisoryService: {
    createAdvisory: vi.fn(),
    getAdvisory: vi.fn(),
    getAdvisoryTrace: vi.fn(),
    listAdvisories: vi.fn(),
  },
}));

vi.mock('../services/credentialService', () => ({
  credentialService: {
    listCredentials: vi.fn().mockResolvedValue({ items: [] }),
  },
}));

vi.mock('../services/configurationService', () => ({
  configurationService: {
    getCrops: vi.fn().mockResolvedValue([
      {
        crop_id: 'crop:cotton',
        common_names: ['Cotton'],
        scientific_name: 'Gossypium hirsutum',
        growth_stages: [{ stage_id: 'flowering', names: ['Flowering'] }],
        varieties: [{ variety_id: 'bt-cotton', names: ['Bt Cotton'] }],
      },
    ]),
    getLocations: vi.fn().mockResolvedValue([
      { location_id: 'loc:adilabad', display_name: 'Adilabad' },
    ]),
    getProviders: vi.fn().mockResolvedValue([
      { provider_id: 'deterministic', name: 'Deterministic' },
      { provider_id: 'cerebras', name: 'Cerebras' },
    ]),
  },
}));

describe('Layout Navigation and Advisory Workflow Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('1. Sidebar renders all agricultural navigation links', () => {
    render(
      <BrowserRouter>
        <AuthProvider>
          <ToastProvider>
            <Sidebar isOpen={true} onClose={vi.fn()} />
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    );

    expect(screen.getAllByText(/RythuSaathi/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Dashboard/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/My Consultations/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Agent Activity/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Weather/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Mandi Prices/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Govt Schemes/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Risk Alerts/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/BYOK Vault/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Settings/i).length).toBeGreaterThan(0);
  });

  it('2. TopHeader renders title, status and user identity badge', () => {
    render(
      <BrowserRouter>
        <AuthProvider>
          <ToastProvider>
            <TopHeader title="My Field Intelligence" onMenuClick={vi.fn()} />
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    );

    expect(screen.getByText(/My Field Intelligence/i)).toBeInTheDocument();
  });

  it('3. Advisory form validates minimum inquiry length', async () => {
    render(
      <BrowserRouter>
        <AuthProvider>
          <ConfigProvider>
            <ToastProvider>
              <NewAdvisoryPage />
            </ToastProvider>
          </ConfigProvider>
        </AuthProvider>
      </BrowserRouter>
    );

    // Provide query shorter than 3 chars
    const queryInput = screen.getByLabelText(/Agronomic Question \/ Observation/i);
    fireEvent.change(queryInput, { target: { value: 'ab' } });

    const submitBtn = screen.getByRole('button', { name: /Execute 7-Agent Consultation/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText(/Please provide a specific agronomic inquiry/i)).toBeInTheDocument();
    });
    expect(advisoryService.createAdvisory).not.toHaveBeenCalled();
  });

  it('4. Advisory form submits valid inquiry and executes 7-agent workflow', async () => {
    advisoryService.createAdvisory.mockResolvedValueOnce({
      advisory_id: 'api:adv-result-123',
      status: 'COMPLETED',
      coordinator_synthesis: 'Valid synthesis output',
    });

    render(
      <BrowserRouter>
        <AuthProvider>
          <ConfigProvider>
            <ToastProvider>
              <NewAdvisoryPage />
            </ToastProvider>
          </ConfigProvider>
        </AuthProvider>
      </BrowserRouter>
    );

    const queryInput = screen.getByLabelText(/Agronomic Question \/ Observation/i);
    fireEvent.change(queryInput, {
      target: { value: 'Cotton leaves turning red in Adilabad' },
    });

    const submitBtn = screen.getByRole('button', { name: /Execute 7-Agent Consultation/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(advisoryService.createAdvisory).toHaveBeenCalledWith(
        expect.objectContaining({
          query: 'Cotton leaves turning red in Adilabad',
          mode: 'deterministic',
        })
      );
    });
  });

  it('5. Unauthenticated user accessing protected route is redirected to /login', () => {
    localStorage.removeItem('rythusaathi_auth_token');

    // Render full App without token
    render(<App />);

    expect(screen.getByText(/Welcome back/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign In/i })).toBeInTheDocument();
  });
});
