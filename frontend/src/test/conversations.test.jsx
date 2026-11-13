import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import { ConversationalAdvisoryPage } from '../pages/chat/ConversationalAdvisoryPage';
import { conversationService } from '../services/conversationService';
import { AuthProvider } from '../context/AuthContext';
import { ConfigProvider } from '../context/ConfigContext';
import { ToastProvider } from '../context/ToastContext';

// Mock conversationService
vi.mock('../services/conversationService', () => ({
  conversationService: {
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    createConversation: vi.fn(),
    sendMessage: vi.fn(),
    deleteConversation: vi.fn(),
  },
  default: {
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    createConversation: vi.fn(),
    sendMessage: vi.fn(),
    deleteConversation: vi.fn(),
  },
}));

// Mock configService
vi.mock('../services/credentialService', () => ({
  credentialService: {
    listCredentials: vi.fn().mockResolvedValue([]),
    enrollCredential: vi.fn(),
    deleteCredential: vi.fn(),
  },
  default: {
    listCredentials: vi.fn().mockResolvedValue([]),
    enrollCredential: vi.fn(),
    deleteCredential: vi.fn(),
  },
}));

// Mock configService
vi.mock('../services/configurationService', () => ({
  configurationService: {
    getCrops: vi.fn().mockResolvedValue([
      { crop_id: 'crop:cereal:rice', common_names: ['Rice'], scientific_name: 'Oryza sativa' },
    ]),
    getLocations: vi.fn().mockResolvedValue([
      { location_id: 'location:district:in-pb-ludhiana', display_name: 'Ludhiana', level: 'district' },
    ]),
    getSystemConfig: vi.fn().mockResolvedValue({}),
  },
}));

describe('Conversational Advisory Chat Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    localStorage.setItem('rythusaathi_token', 'test-token');
    localStorage.setItem(
      'rythusaathi_user',
      JSON.stringify({
        user_id: 'farmer_pb_01',
        tenant_id: 'default',
        roles: ['farmer'],
      })
    );
  });

  const renderComponent = () =>
    render(
      <BrowserRouter>
        <AuthProvider>
          <ConfigProvider>
            <ToastProvider>
              <ConversationalAdvisoryPage />
            </ToastProvider>
          </ConfigProvider>
        </AuthProvider>
      </BrowserRouter>
    );

  it('1. Renders empty conversation welcome state with sample prompt chips', async () => {
    conversationService.listConversations.mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    renderComponent();

    await waitFor(() => {
      expect(screen.getByText(/Welcome to RythuSaathi Assistant/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Rice Brown Leaf Spots/i)).toBeInTheDocument();
    expect(screen.getByText(/Adilabad Cotton Mandi Price/i)).toBeInTheDocument();
    expect(screen.getByText(/Punjab Govt Schemes/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Ask about crop health, weather/i)).toBeInTheDocument();
  });

  it('2. Loads past conversations list and renders messages on selection', async () => {
    const mockConv = {
      conversation_id: 'conv-123',
      title: 'Rice leaf spots inquiry',
      context: {
        crop: 'Rice',
        location: 'Ludhiana',
        growth_stage: 'Tillering',
        variety: null,
      },
      messages: [
        {
          message_id: 'msg-1',
          conversation_id: 'conv-123',
          role: 'user',
          content: 'Rice leaves are showing brown leaf spots during tillering stage.',
          created_at: new Date().toISOString(),
        },
        {
          message_id: 'msg-2',
          conversation_id: 'conv-123',
          role: 'assistant',
          content: 'Apply propiconazole or organic neem formulations safely for tillering rice in Ludhiana.',
          agent_telemetry: [
            { agent_id: 'Intake', status: 'completed', duration_ms: 12, model_id: 'heuristic' },
            { agent_id: 'Coordinator', status: 'completed', duration_ms: 45, model_id: 'groq/llama-3.3-70b-versatile' },
          ],
          weather_evidence: [{ evidence_id: 'ev-w-1', source_id: 'open-meteo' }],
          market_evidence: [],
          scheme_evidence: [{ evidence_id: 'ev-s-1', source_id: 'pmfby' }],
          active_risk_alerts: [
            {
              finding_id: 'risk-1',
              category: 'weather',
              title: 'Excessive Rainfall Advisory',
              severity: 'HIGH',
              explanation: 'High precipitation forecast within 48h.',
              safe_action: 'Ensure field drainage channels are clear.',
            },
          ],
          suggested_next_steps: ['Check field water drainage', 'Monitor for leaf spot spread'],
          created_at: new Date().toISOString(),
        },
      ],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    conversationService.listConversations.mockResolvedValueOnce({
      items: [
        {
          conversation_id: 'conv-123',
          title: 'Rice leaf spots inquiry',
          message_count: 2,
          context: mockConv.context,
          updated_at: mockConv.updated_at,
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });

    conversationService.getConversation.mockResolvedValueOnce(mockConv);

    renderComponent();

    await waitFor(() => {
      expect(screen.getByText(/Rice leaves are showing brown leaf spots/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Apply propiconazole or organic neem formulations/i)).toBeInTheDocument();
    expect(screen.getByText(/Open-Meteo Weather/i)).toBeInTheDocument();
    expect(screen.getByText(/Govt Schemes/i)).toBeInTheDocument();
    expect(screen.getByText(/Excessive Rainfall Advisory/i)).toBeInTheDocument();
    expect(screen.getByText(/Ensure field drainage channels are clear/i)).toBeInTheDocument();
    expect(screen.getByText(/Check field water drainage/i)).toBeInTheDocument();
  });

  it('3. Submitting user prompt creates a conversation and displays response', async () => {
    conversationService.listConversations.mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    const createdConv = {
      conversation_id: 'conv-new-999',
      title: 'Adilabad cotton price',
      context: {
        crop: 'Cotton',
        location: 'Adilabad',
        growth_stage: null,
        variety: null,
      },
      messages: [
        {
          message_id: 'msg-u1',
          conversation_id: 'conv-new-999',
          role: 'user',
          content: 'What is the cotton mandi price in Adilabad?',
          created_at: new Date().toISOString(),
        },
        {
          message_id: 'msg-a1',
          conversation_id: 'conv-new-999',
          role: 'assistant',
          content: 'Adilabad APMC reports modal price of Rs 8,870/quintal for Cotton.',
          agent_telemetry: [
            { agent_id: 'Market', status: 'completed', duration_ms: 10 },
          ],
          market_evidence: [{ evidence_id: 'ev-m-1', source_id: 'data-gov-in' }],
          suggested_next_steps: ['Check historical price trends'],
          created_at: new Date().toISOString(),
        },
      ],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    conversationService.createConversation.mockResolvedValueOnce(createdConv);

    renderComponent();

    const textarea = screen.getByPlaceholderText(/Ask about crop health, weather/i);
    fireEvent.change(textarea, { target: { value: 'What is the cotton mandi price in Adilabad?' } });

    const sendBtn = screen.getByRole('button', { name: /Send/i });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(conversationService.createConversation).toHaveBeenCalledWith(
        expect.objectContaining({
          initial_message: 'What is the cotton mandi price in Adilabad?',
        })
      );
    });

    await waitFor(() => {
      expect(screen.getByText(/Adilabad APMC reports modal price/i)).toBeInTheDocument();
    });
  });

  it('4. Allows toggling parameter drawer to specify crop and location', async () => {
    conversationService.listConversations.mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    renderComponent();

    const adjustBtn = screen.getByRole('button', { name: /Adjust Field Context/i });
    fireEvent.click(adjustBtn);

    expect(screen.getByLabelText(/Crop/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Location/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Growth Stage/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Variety/i)).toBeInTheDocument();

    const cropInput = screen.getByLabelText(/Crop/i);
    fireEvent.change(cropInput, { target: { value: 'Wheat' } });
    expect(cropInput.value).toBe('Wheat');
  });

  it('5. Allows changing execution mode and passes mode in createConversation', async () => {
    conversationService.listConversations.mockResolvedValueOnce({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });

    const createdConv = {
      conversation_id: 'conv-llm-1',
      title: 'LLM Consultation',
      messages: [
        {
          message_id: 'msg-u1',
          role: 'user',
          content: 'Hello agent',
          created_at: new Date().toISOString(),
        },
        {
          message_id: 'msg-a1',
          role: 'assistant',
          content: 'LLM response synthesized',
          created_at: new Date().toISOString(),
        },
      ],
    };
    conversationService.createConversation.mockResolvedValueOnce(createdConv);

    renderComponent();

    const modeSelect = screen.getByTestId('execution-mode-select');
    expect(modeSelect.value).toBe('deterministic');

    fireEvent.change(modeSelect, { target: { value: 'groq' } });
    expect(modeSelect.value).toBe('groq');

    const textarea = screen.getByPlaceholderText(/Ask about crop health, weather/i);
    fireEvent.change(textarea, { target: { value: 'Hello agent' } });

    const sendBtn = screen.getByRole('button', { name: /Send/i });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      expect(conversationService.createConversation).toHaveBeenCalledWith(
        expect.objectContaining({
          initial_message: 'Hello agent',
          mode: 'groq',
        })
      );
    });
  });

  it('6. Renders specialist agent summary and skip reasons in telemetry drawer', async () => {
    const mockConv = {
      conversation_id: 'conv-telemetry-1',
      title: 'Telemetry test',
      messages: [
        {
          message_id: 'msg-u1',
          role: 'user',
          content: 'Rice brown spots',
          created_at: new Date().toISOString(),
        },
        {
          message_id: 'msg-a1',
          role: 'assistant',
          content: 'Advisory recommendations',
          agent_telemetry: [
            {
              agent_id: 'Crop',
              status: 'completed',
              duration_ms: 22,
              summary: 'Retrieved 2 general crop-scoped source excerpt(s) without location refinement.',
            },
            {
              agent_id: 'Weather',
              status: 'skipped',
              duration_ms: 1,
              summary: 'Weather lookup requires a registered location; none was supplied.',
            },
            {
              agent_id: 'Scheme',
              status: 'skipped',
              duration_ms: 1,
              summary: 'No government scheme inquiry was requested; scheme lookup is skipped.',
            },
          ],
          created_at: new Date().toISOString(),
        },
      ],
    };

    conversationService.listConversations.mockResolvedValueOnce({
      items: [{ conversation_id: 'conv-telemetry-1', title: 'Telemetry test', message_count: 2 }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    conversationService.getConversation.mockResolvedValueOnce(mockConv);

    renderComponent();

    await waitFor(() => {
      expect(screen.getByText(/Advisory recommendations/i)).toBeInTheDocument();
    });

    const accordionBtn = screen.getByRole('button', { name: /7-Agent Telemetry & Execution Trace/i });
    fireEvent.click(accordionBtn);

    await waitFor(() => {
      expect(
        screen.getByText(/Retrieved 2 general crop-scoped source excerpt\(s\) without location refinement/i)
      ).toBeInTheDocument();
      expect(
        screen.getByText(/Weather lookup requires a registered location; none was supplied/i)
      ).toBeInTheDocument();
      expect(
        screen.getByText(/No government scheme inquiry was requested; scheme lookup is skipped/i)
      ).toBeInTheDocument();
    });
  });
});

