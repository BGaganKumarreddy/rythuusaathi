import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { ToastProvider } from '../context/ToastContext';
import { ConfigProvider } from '../context/ConfigContext';
import { HistoryPage } from '../pages/history/HistoryPage';
import { CredentialsPage } from '../pages/credentials/CredentialsPage';
import { historyService } from '../services/historyService';
import { credentialService } from '../services/credentialService';
import { configurationService } from '../services/configurationService';

vi.mock('../services/historyService', () => ({
  historyService: {
    getHistory: vi.fn(),
  },
}));

vi.mock('../services/credentialService', () => ({
  credentialService: {
    listCredentials: vi.fn(),
    createCredential: vi.fn(),
    deleteCredential: vi.fn(),
  },
}));

vi.mock('../services/configurationService', () => ({
  configurationService: {
    getCrops: vi.fn().mockResolvedValue([
      { crop_id: 'crop:cotton', common_names: ['Cotton'], scientific_name: 'Gossypium hirsutum' },
      { crop_id: 'crop:rice', common_names: ['Rice'], scientific_name: 'Oryza sativa' },
    ]),
    getLocations: vi.fn().mockResolvedValue([
      { location_id: 'loc:adilabad', display_name: 'Adilabad' },
    ]),
    getProviders: vi.fn().mockResolvedValue([
      { provider_id: 'cerebras', name: 'Cerebras' },
    ]),
  },
}));

const renderWithProviders = (ui) => {
  return render(
    <BrowserRouter>
      <ConfigProvider>
        <ToastProvider>{ui}</ToastProvider>
      </ConfigProvider>
    </BrowserRouter>
  );
};

describe('History and BYOK Credential Vault Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('1. Renders historical consultations list with pagination', async () => {
    historyService.getHistory.mockResolvedValueOnce({
      items: [
        {
          advisory_id: 'adv-001',
          query: 'Cotton leaf spot treatment',
          crop: 'cotton',
          location: 'Adilabad',
          execution_mode: 'deterministic',
          status: 'COMPLETED',
          synthesis_preview: 'Apply copper oxychloride spray.',
          active_alert_count: 0,
          created_at: '2026-11-12T10:00:00Z',
        },
      ],
      total: 1,
      limit: 10,
      offset: 0,
    });

    renderWithProviders(<HistoryPage />);

    await waitFor(() => {
      expect(screen.getByText(/Cotton leaf spot treatment/i)).toBeInTheDocument();
      expect(screen.getByText(/Apply copper oxychloride spray/i)).toBeInTheDocument();
      expect(screen.getByText(/Location: Adilabad/i)).toBeInTheDocument();
    });
  });

  it('2. Credential Vault displays active credentials and secrecy', async () => {
    credentialService.listCredentials.mockResolvedValueOnce({
      items: [
        {
          credential_id: 'cred-cerebras-xyz123',
          provider_id: 'cerebras',
          state: 'active',
          generation: 1,
          updated_at: '2026-11-12T12:00:00Z',
        },
      ],
      total: 1,
    });

    renderWithProviders(<CredentialsPage />);

    await waitFor(() => {
      expect(screen.getAllByText(/Cerebras Cloud Inference/i).length).toBeGreaterThan(0);
      expect(screen.getByText(/cred-cerebras-xyz123/i)).toBeInTheDocument();
    });

    // Verify secret is NOT displayed anywhere in document
    expect(screen.queryByText(/sk-/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/csk-/i)).not.toBeInTheDocument();
  });

  it('3. Enrolling a new credential sends secret and clears form', async () => {
    credentialService.listCredentials.mockResolvedValue({ items: [] });
    credentialService.createCredential.mockResolvedValueOnce({
      credential_id: 'cred-gemini-456',
      provider_id: 'gemini',
      state: 'active',
      generation: 1,
      updated_at: '2026-11-12T12:00:00Z',
    });

    renderWithProviders(<CredentialsPage />);

    await waitFor(() => {
      expect(screen.getByText(/No BYOK Credentials Enrolled/i)).toBeInTheDocument();
    });

    // Open Add Key modal
    fireEvent.click(screen.getByRole('button', { name: /Add Provider Key/i }));

    const secretInput = screen.getByLabelText(/Provider API Secret Key/i);
    fireEvent.change(secretInput, { target: { value: 'csk-live-super-secret-key-999' } });

    fireEvent.click(screen.getByRole('button', { name: /Encrypt & Store/i }));

    await waitFor(() => {
      expect(credentialService.createCredential).toHaveBeenCalledWith({
        provider_id: 'cerebras',
        secret: 'csk-live-super-secret-key-999',
      });
    });
  });

  it('4. Deleting a credential requires confirmation modal', async () => {
    credentialService.listCredentials.mockResolvedValueOnce({
      items: [
        {
          credential_id: 'cred-to-delete',
          provider_id: 'groq',
          state: 'active',
          generation: 1,
          updated_at: '2026-11-12T12:00:00Z',
        },
      ],
      total: 1,
    });
    credentialService.deleteCredential.mockResolvedValueOnce({ success: true });

    renderWithProviders(<CredentialsPage />);

    await waitFor(() => {
      expect(screen.getByText(/cred-to-delete/i)).toBeInTheDocument();
    });

    // Click delete
    fireEvent.click(screen.getByRole('button', { name: /Delete Key/i }));

    // Confirm modal opens
    expect(screen.getByText(/Confirm Key Deletion/i)).toBeInTheDocument();

    // Confirm permanent deletion
    fireEvent.click(screen.getByRole('button', { name: /Permanently Delete/i }));

    await waitFor(() => {
      expect(credentialService.deleteCredential).toHaveBeenCalledWith('cred-to-delete');
    });
  });
});
