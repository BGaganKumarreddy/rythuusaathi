import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import {
  Sparkles,
  Send,
  Plus,
  Trash2,
  ChevronDown,
  ChevronUp,
  Cpu,
  CloudSun,
  Sprout,
  TrendingUp,
  Landmark,
  ShieldAlert,
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  XCircle,
  Clock,
  Compass,
  MapPin,
  Layers,
  Calendar,
  MessageSquare,
  Bot,
  User,
  ArrowRight,
  RefreshCw,
  ExternalLink,
} from 'lucide-react';
import { conversationService } from '../../services/conversationService';
import { credentialService } from '../../services/credentialService';
import { useConfig } from '../../context/ConfigContext';
import { useToast } from '../../context/ToastContext';
import { Badge } from '../../components/ui/Badge';
import { Button } from '../../components/ui/Button';
import { Input, Select } from '../../components/ui/Input';

const SAMPLE_PROMPTS = [
  {
    title: 'Rice Brown Leaf Spots',
    text: 'Rice leaves are showing brown leaf spots during tillering stage. What safe organic controls are available?',
    category: 'Crop Health',
  },
  {
    title: 'Adilabad Cotton Mandi Price',
    text: 'What is the current mandi price for cotton in Adilabad APMC?',
    category: 'Mandi Rates',
  },
  {
    title: 'Punjab Govt Schemes',
    text: 'My field is in Ludhiana, Punjab. What government schemes and subsidies can I apply for?',
    category: 'Schemes',
  },
  {
    title: 'Weather & Spraying Advisory',
    text: 'Will it rain in Ludhiana in the next 3 days? Is it safe to spray fungicide today?',
    category: 'Weather',
  },
];

export const ConversationalAdvisoryPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { crops, locations } = useConfig();

  // Active conversation state
  const conversationIdFromUrl = searchParams.get('id');
  const [conversations, setConversations] = useState([]);
  const [activeConversation, setActiveConversation] = useState(null);
  const [isLoadingConvList, setIsLoadingConvList] = useState(true);
  const [isLoadingActiveConv, setIsLoadingActiveConv] = useState(false);

  // Message composer state
  const [inputText, setInputText] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [sendStep, setSendStep] = useState(0);

  // Execution mode & active provider credentials
  const [executionMode, setExecutionMode] = useState('deterministic');
  const [activeCredentials, setActiveCredentials] = useState([]);

  // Manual parameter drawer / overrides
  const [showParamDrawer, setShowParamDrawer] = useState(false);
  const [overrideCrop, setOverrideCrop] = useState('');
  const [overrideLocation, setOverrideLocation] = useState('');
  const [overrideStage, setOverrideStage] = useState('');
  const [overrideVariety, setOverrideVariety] = useState('');

  // Expanded telemetry map for message IDs
  const [expandedTelemetry, setExpandedTelemetry] = useState({});

  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);

  const scrollToBottom = () => {
    if (messagesEndRef.current && typeof messagesEndRef.current.scrollIntoView === 'function') {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  };

  useEffect(() => {
    scrollToBottom();
  }, [activeConversation?.messages, isSending]);

  // Load available active BYOK credentials
  useEffect(() => {
    credentialService
      .listCredentials()
      .then((creds) => {
        if (Array.isArray(creds)) {
          setActiveCredentials(creds.filter((c) => c.state === 'active'));
        }
      })
      .catch((err) => console.warn('Could not list credentials:', err));
  }, []);

  // Load conversation list on mount
  useEffect(() => {
    loadConversationList();
  }, []);

  // When URL param changes or list loads, load the active conversation
  useEffect(() => {
    if (conversationIdFromUrl) {
      loadSpecificConversation(conversationIdFromUrl);
    } else {
      setActiveConversation(null);
    }
  }, [conversationIdFromUrl]);

  const loadConversationList = async () => {
    try {
      setIsLoadingConvList(true);
      const res = await conversationService.listConversations({ limit: 50 });
      setConversations(res.items || []);

      // If no active ID in URL but we have conversations, select the first one
      if (!conversationIdFromUrl && res.items && res.items.length > 0) {
        const firstId = res.items[0].conversation_id;
        setSearchParams({ id: firstId }, { replace: true });
        loadSpecificConversation(firstId);
      }
    } catch (err) {
      console.error('Failed to load conversation list:', err);
    } finally {
      setIsLoadingConvList(false);
    }
  };

  const loadSpecificConversation = async (convId) => {
    try {
      setIsLoadingActiveConv(true);
      const conv = await conversationService.getConversation(convId);
      setActiveConversation(conv);
      // Prepopulate override inputs with current confirmed context
      if (conv?.context) {
        setOverrideCrop(conv.context.crop || '');
        setOverrideLocation(conv.context.location || '');
        setOverrideStage(conv.context.growth_stage || '');
        setOverrideVariety(conv.context.variety || '');
      }
    } catch (err) {
      console.error('Failed to load conversation:', err);
      toast.error('Unable to load consultation thread');
      setActiveConversation(null);
    } finally {
      setIsLoadingActiveConv(false);
    }
  };

  const handleStartNewConsultation = () => {
    setActiveConversation(null);
    setSearchParams({});
    setInputText('');
    setOverrideCrop('');
    setOverrideLocation('');
    setOverrideStage('');
    setOverrideVariety('');
  };

  const handleDeleteConversation = async (e, convId) => {
    e.stopPropagation();
    if (!window.confirm('Are you sure you want to delete this consultation thread?')) {
      return;
    }
    try {
      await conversationService.deleteConversation(convId);
      toast.success('Consultation deleted');
      setConversations((prev) => prev.filter((c) => c.conversation_id !== convId));
      if (conversationIdFromUrl === convId) {
        handleStartNewConsultation();
      }
    } catch (err) {
      toast.error(err?.message || 'Failed to delete conversation');
    }
  };

  const handleSendMessage = async (customText = null) => {
    const textToSend = (customText !== null ? customText : inputText).trim();
    if (!textToSend || isSending) return;

    setInputText('');
    setIsSending(true);
    setSendStep(1);

    const stepInterval = setInterval(() => {
      setSendStep((prev) => (prev < 6 ? prev + 1 : prev));
    }, 1100);

    try {
      if (!activeConversation) {
        // Create new conversation with initial message
        const newConv = await conversationService.createConversation({
          initial_message: textToSend,
          mode: executionMode,
          crop: overrideCrop.trim() || undefined,
          location: overrideLocation.trim() || undefined,
          growth_stage: overrideStage.trim() || undefined,
          variety: overrideVariety.trim() || undefined,
        });

        clearInterval(stepInterval);
        setIsSending(false);
        setActiveConversation(newConv);
        setConversations((prev) => [
          {
            conversation_id: newConv.conversation_id,
            title: newConv.title,
            message_count: newConv.messages.length,
            context: newConv.context,
            updated_at: newConv.updated_at,
          },
          ...prev.filter((c) => c.conversation_id !== newConv.conversation_id),
        ]);
        setSearchParams({ id: newConv.conversation_id });
        toast.success('Consultation started', '7 Agents Coordinated');
      } else {
        // Optimistically add user message to UI
        const optimisticUserMsg = {
          message_id: `temp-${Date.now()}`,
          conversation_id: activeConversation.conversation_id,
          role: 'user',
          content: textToSend,
          created_at: new Date().toISOString(),
        };

        setActiveConversation((prev) => ({
          ...prev,
          messages: [...(prev.messages || []), optimisticUserMsg],
        }));

        // Send follow-up message to backend
        const asstMsg = await conversationService.sendMessage(
          activeConversation.conversation_id,
          {
            content: textToSend,
            mode: executionMode,
            crop: overrideCrop.trim() || undefined,
            location: overrideLocation.trim() || undefined,
            growth_stage: overrideStage.trim() || undefined,
            variety: overrideVariety.trim() || undefined,
          }
        );

        clearInterval(stepInterval);
        setIsSending(false);

        // Reload conversation from server to update context state & timestamps
        const updatedConv = await conversationService.getConversation(
          activeConversation.conversation_id
        );
        setActiveConversation(updatedConv);

        // Refresh list summary
        setConversations((prev) =>
          prev.map((c) =>
            c.conversation_id === updatedConv.conversation_id
              ? {
                  ...c,
                  title: updatedConv.title,
                  message_count: updatedConv.messages.length,
                  context: updatedConv.context,
                  updated_at: updatedConv.updated_at,
                }
              : c
          )
        );
      }
    } catch (err) {
      clearInterval(stepInterval);
      setIsSending(false);
      console.error('Error sending message:', err);
      const errMsg = err?.response?.data?.message || err?.message || 'Failed to generate advisory response';
      const errCode = err?.response?.data?.code || err?.code;
      if (errCode === 'missing_provider_credential' || errMsg.includes('BYOK') || errMsg.includes('missing_provider_credential')) {
        toast.error(errMsg, 'Missing Provider Credential', {
          action: {
            label: 'Open Vault',
            onClick: () => navigate('/credentials'),
          },
        });
      } else {
        toast.error(errMsg);
      }
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const toggleTelemetry = (msgId) => {
    setExpandedTelemetry((prev) => ({
      ...prev,
      [msgId]: !prev[msgId],
    }));
  };

  // Helper for agent icons
  const getAgentStatusBadge = (status) => {
    switch (status?.toLowerCase()) {
      case 'completed':
        return <Badge variant="completed" size="sm">Completed</Badge>;
      case 'partial':
        return <Badge variant="partial" size="sm">Partial</Badge>;
      case 'unavailable':
        return <Badge variant="unavailable" size="sm">Unavailable</Badge>;
      case 'failed':
        return <Badge variant="failed" size="sm">Failed</Badge>;
      case 'skipped':
        return <Badge variant="neutral" size="sm">Skipped</Badge>;
      default:
        return <Badge variant="neutral" size="sm">Pending</Badge>;
    }
  };

  return (
    <div className="flex flex-col lg:flex-row h-[calc(100vh-8rem)] bg-white rounded-3xl border border-[#E6EAE4] shadow-xs overflow-hidden">
      {/* LEFT SIDEBAR: Consultations History List */}
      <div className="w-full lg:w-80 border-b lg:border-b-0 lg:border-r border-[#E6EAE4] bg-[#FDFEFC] flex flex-col shrink-0">
        {/* Top Action Header */}
        <div className="p-4 border-b border-[#E6EAE4] flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-[#DCE9DC] flex items-center justify-center text-[#183D2B]">
              <MessageSquare className="w-4 h-4 text-[#347A50]" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-[#202B24]">Consultations</h2>
              <p className="text-[11px] text-[#78837B]">Multi-Turn Advisory Sessions</p>
            </div>
          </div>
          <Button
            size="sm"
            variant="primary"
            onClick={handleStartNewConsultation}
            icon={Plus}
          >
            New
          </Button>
        </div>

        {/* Conversation List */}
        <div className="flex-1 overflow-y-auto divide-y divide-[#F1F4EE] p-2 space-y-1">
          {isLoadingConvList ? (
            <div className="p-6 text-center text-xs text-[#78837B] space-y-2">
              <div className="w-6 h-6 border-2 border-[#347A50] border-t-transparent rounded-full animate-spin mx-auto" />
              <p>Loading consultations...</p>
            </div>
          ) : conversations.length === 0 ? (
            <div className="p-6 text-center text-xs text-[#78837B] space-y-2">
              <Sprout className="w-8 h-8 text-[#347A50]/40 mx-auto" />
              <p className="font-semibold text-[#202B24]">No Consultations Yet</p>
              <p className="text-[11px]">Start a conversation below with your agricultural question.</p>
            </div>
          ) : (
            conversations.map((c) => {
              const isSelected = activeConversation?.conversation_id === c.conversation_id;
              return (
                <div
                  key={c.conversation_id}
                  onClick={() => setSearchParams({ id: c.conversation_id })}
                  className={`group relative p-3 rounded-2xl cursor-pointer transition-all ${
                    isSelected
                      ? 'bg-[#EBF2EB] border border-[#C6DCB8] text-[#183D2B]'
                      : 'hover:bg-[#F4F7F2] text-[#202B24]'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-xs font-bold truncate flex-1 leading-snug">
                      {c.title || 'Untitled Consultation'}
                    </p>
                    <button
                      onClick={(e) => handleDeleteConversation(e, c.conversation_id)}
                      title="Delete conversation"
                      className="opacity-0 group-hover:opacity-100 p-1 text-[#78837B] hover:text-red-600 rounded-lg transition-opacity"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                    {c.context?.crop && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-[#DCE9DC] text-[#183D2B] font-medium">
                        🌾 {c.context.crop}
                      </span>
                    )}
                    {c.context?.location && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-slate-100 text-slate-700 font-medium">
                        📍 {c.context.location}
                      </span>
                    )}
                    {c.message_count > 0 && (
                      <span className="text-[10px] text-[#78837B] ml-auto">
                        {c.message_count} msgs
                      </span>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* MAIN VIEWPORT: Chat Messages & Input Composer */}
      <div className="flex-1 flex flex-col min-w-0 bg-[#FDFEFC]">
        {/* Top Header of Chat */}
        <div className="px-6 py-4 border-b border-[#E6EAE4] bg-white flex items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-base font-extrabold text-[#202B24] truncate">
                {activeConversation?.title || 'New Agricultural Consultation'}
              </h1>
              <Badge variant="active" size="sm">7 Agents Live</Badge>
            </div>
            <p className="text-xs text-[#78837B] truncate mt-0.5">
              Evidence-grounded agronomy, weather, mandi market, and government scheme synthesis
            </p>
          </div>

          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 bg-[#F7F8F4] px-2.5 py-1.5 rounded-xl border border-[#E6EAE4]">
              <Cpu className="w-3.5 h-3.5 text-[#347A50]" />
              <label htmlFor="execution-mode-select" className="sr-only">
                Execution Mode
              </label>
              <select
                id="execution-mode-select"
                data-testid="execution-mode-select"
                value={executionMode}
                onChange={(e) => setExecutionMode(e.target.value)}
                className="bg-transparent text-xs font-bold text-[#183D2B] focus:outline-hidden cursor-pointer"
              >
                <option value="deterministic">Deterministic Baseline</option>
                <option value="groq">Groq Cloud LPU</option>
                <option value="cerebras">Cerebras Wafer-Scale</option>
                <option value="gemini">Google Gemini AI Studio</option>
              </select>
            </div>
            <Button
              size="sm"
              variant={showParamDrawer ? 'primary' : 'outline'}
              onClick={() => setShowParamDrawer((p) => !p)}
              icon={Compass}
            >
              {showParamDrawer ? 'Close Field Context' : 'Adjust Field Context'}
            </Button>
          </div>
        </div>

        {/* Optional Collapsible Field Context Drawer */}
        {showParamDrawer && (
          <div className="p-4 border-b border-[#E6EAE4] bg-[#F7F8F4] animate-in slide-in-from-top-2 duration-200">
            <div className="max-w-4xl mx-auto space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold text-[#202B24] uppercase tracking-wider">
                  Active Consultation Context (Precedence: Explicit correction &gt; confirmed &gt; auto-detected)
                </span>
                <span className="text-[11px] text-[#78837B]">
                  Leave blank to allow natural-language auto-detection
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-4 gap-3">
                <Input
                  label="Crop"
                  id="overrideCrop"
                  placeholder="e.g. Rice, Cotton, Wheat"
                  value={overrideCrop}
                  onChange={(e) => setOverrideCrop(e.target.value)}
                />
                <Input
                  label="Location"
                  id="overrideLocation"
                  placeholder="e.g. Ludhiana, Adilabad"
                  value={overrideLocation}
                  onChange={(e) => setOverrideLocation(e.target.value)}
                />
                <Input
                  label="Growth Stage"
                  id="overrideStage"
                  placeholder="e.g. Tillering, Flowering"
                  value={overrideStage}
                  onChange={(e) => setOverrideStage(e.target.value)}
                />
                <Input
                  label="Variety"
                  id="overrideVariety"
                  placeholder="e.g. PR 126, HD 2967"
                  value={overrideVariety}
                  onChange={(e) => setOverrideVariety(e.target.value)}
                />
              </div>
            </div>
          </div>
        )}

        {/* Chat Scroll Viewport */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-6">
          {isLoadingActiveConv ? (
            <div className="h-full flex items-center justify-center">
              <div className="flex flex-col items-center gap-3">
                <div className="w-8 h-8 border-3 border-[#347A50] border-t-transparent rounded-full animate-spin" />
                <p className="text-xs font-semibold text-[#183D2B]">Loading consultation messages...</p>
              </div>
            </div>
          ) : !activeConversation || activeConversation.messages?.length === 0 ? (
            /* EMPTY STATE: Welcoming Banner & Suggested Prompts */
            <div className="max-w-2xl mx-auto my-auto py-10 space-y-8 text-center animate-in fade-in duration-300">
              <div className="space-y-3">
                <div className="w-16 h-16 rounded-3xl bg-[#DCE9DC] text-[#183D2B] mx-auto flex items-center justify-center shadow-inner">
                  <Sprout className="w-9 h-9 text-[#347A50]" />
                </div>
                <h2 className="text-2xl font-extrabold text-[#202B24] tracking-tight">
                  Welcome to RythuSaathi Assistant
                </h2>
                <p className="text-sm text-[#78837B] max-w-lg mx-auto leading-relaxed">
                  Ask any question about your field in plain language. Our 7 specialist AI agents will collaborate on your shared farm board to provide evidence-backed recommendations.
                </p>
              </div>

              {/* Sample Prompts Grid */}
              <div className="text-left space-y-3">
                <p className="text-xs font-bold text-[#78837B] uppercase tracking-wider flex items-center gap-2">
                  <Sparkles className="w-3.5 h-3.5 text-[#347A50]" />
                  <span>Try Asking One of These Common Questions</span>
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {SAMPLE_PROMPTS.map((sample, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => handleSendMessage(sample.text)}
                      className="p-4 rounded-2xl bg-white border border-[#E6EAE4] hover:border-[#347A50] hover:bg-[#F7F8F4] text-left transition-all group shadow-xs hover:shadow-sm"
                    >
                      <div className="flex items-center justify-between text-xs font-bold text-[#202B24] group-hover:text-[#183D2B]">
                        <span>{sample.title}</span>
                        <span className="text-[10px] text-[#347A50] font-bold group-hover:translate-x-1 transition-transform">
                          Ask &rarr;
                        </span>
                      </div>
                      <p className="text-xs text-[#78837B] mt-1.5 line-clamp-2 leading-relaxed">
                        "{sample.text}"
                      </p>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            /* CONVERSATION MESSAGES */
            activeConversation.messages.map((msg, idx) => {
              const isUser = msg.role === 'user';
              const msgKey = msg.message_id || `idx-${idx}`;
              const isExpanded = expandedTelemetry[msgKey] || false;

              if (isUser) {
                return (
                  <div key={msgKey} className="flex justify-end gap-3 max-w-3xl ml-auto animate-in fade-in">
                    <div className="space-y-1.5 text-right">
                      <div className="inline-block bg-[#183D2B] text-white p-4 rounded-3xl rounded-tr-xs shadow-xs text-sm leading-relaxed text-left max-w-xl">
                        {msg.content}
                      </div>
                      <div className="flex items-center justify-end gap-2 text-[11px] text-[#78837B]">
                        {msg.intent && (
                          <span className="capitalize px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 font-medium">
                            Intent: {msg.intent.replace('_', ' ')}
                          </span>
                        )}
                        <span>
                          {msg.created_at ? new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Just now'}
                        </span>
                      </div>
                    </div>
                    <div className="w-8 h-8 rounded-full bg-[#183D2B] text-white flex items-center justify-center shrink-0 text-xs font-bold shadow-xs">
                      <User className="w-4 h-4 text-[#DCE9DC]" />
                    </div>
                  </div>
                );
              }

              // Assistant Message
              return (
                <div key={msgKey} className="flex justify-start gap-3 max-w-4xl mr-auto animate-in fade-in">
                  <div className="w-8 h-8 rounded-full bg-[#347A50] text-white flex items-center justify-center shrink-0 shadow-xs">
                    <Sprout className="w-4 h-4 text-[#DCE9DC]" />
                  </div>

                  <div className="flex-1 space-y-4">
                    {/* Main Assistant Card */}
                    <div className="bg-white border border-[#E6EAE4] rounded-3xl rounded-tl-xs p-5 sm:p-6 shadow-xs space-y-4 text-[#202B24]">
                      {/* Evidence Badges Bar */}
                      <div className="flex items-center gap-2 flex-wrap pb-3 border-b border-[#F1F4EE]">
                        <span className="text-[11px] font-bold text-[#78837B] uppercase tracking-wider">
                          Consulted Specialists:
                        </span>
                        {msg.weather_evidence?.length > 0 && (
                          <Badge variant="active" size="sm">
                            <CloudSun className="w-3 h-3 mr-1 text-[#347A50]" />
                            Open-Meteo Weather
                          </Badge>
                        )}
                        {msg.market_evidence?.length > 0 && (
                          <Badge variant="active" size="sm">
                            <TrendingUp className="w-3 h-3 mr-1 text-[#347A50]" />
                            Agmarknet Mandi Rates
                          </Badge>
                        )}
                        {msg.scheme_evidence?.length > 0 && (
                          <Badge variant="active" size="sm">
                            <Landmark className="w-3 h-3 mr-1 text-[#347A50]" />
                            Govt Schemes (PMFBY / PM-KISAN)
                          </Badge>
                        )}
                        <Badge variant="completed" size="sm">
                          <Cpu className="w-3 h-3 mr-1 text-emerald-600" />
                          ICAR Grounded
                        </Badge>
                      </div>

                      {/* Synthesis Text Body */}
                      <div className="prose prose-sm max-w-none text-sm text-[#202B24] leading-relaxed whitespace-pre-line space-y-3 font-normal">
                        {msg.content}
                      </div>

                      {/* Active Risk Alerts (If Any) */}
                      {msg.active_risk_alerts && msg.active_risk_alerts.length > 0 && (
                        <div className="space-y-2 pt-3 border-t border-[#F1F4EE]">
                          <p className="text-xs font-bold text-amber-900 uppercase tracking-wider flex items-center gap-1.5">
                            <ShieldAlert className="w-4 h-4 text-amber-600" />
                            <span>Active Field Risk Alerts</span>
                          </p>
                          <div className="space-y-2">
                            {msg.active_risk_alerts.map((alert, aIdx) => {
                              const sev = (alert.severity || 'medium').toLowerCase();
                              return (
                                <div
                                  key={aIdx}
                                  className={`p-3.5 rounded-2xl border text-xs space-y-1.5 ${
                                    sev === 'critical'
                                      ? 'bg-red-50 border-red-200 text-red-950'
                                      : sev === 'high'
                                      ? 'bg-amber-50 border-amber-200 text-amber-950'
                                      : 'bg-yellow-50 border-yellow-200 text-yellow-950'
                                  }`}
                                >
                                  <div className="flex items-center justify-between gap-2">
                                    <span className="font-bold flex items-center gap-1.5">
                                      <AlertTriangle className="w-3.5 h-3.5" />
                                      {alert.title}
                                    </span>
                                    <Badge variant={sev} size="sm">
                                      {alert.severity}
                                    </Badge>
                                  </div>
                                  <p className="text-[11px] leading-relaxed opacity-90">
                                    {alert.explanation}
                                  </p>
                                  {alert.safe_action && (
                                    <div className="pt-1 mt-1 border-t border-black/10 flex items-start gap-1 font-semibold text-[11px]">
                                      <span>Safe Action:</span>
                                      <span>{alert.safe_action}</span>
                                    </div>
                                  )}
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Suggested Next Steps / Follow-ups */}
                      {msg.suggested_next_steps && msg.suggested_next_steps.length > 0 && (
                        <div className="space-y-2 pt-3 border-t border-[#F1F4EE]">
                          <p className="text-xs font-bold text-[#78837B] uppercase tracking-wider flex items-center gap-1.5">
                            <Sparkles className="w-3.5 h-3.5 text-[#347A50]" />
                            <span>Suggested Follow-up Questions</span>
                          </p>
                          <div className="flex flex-wrap gap-2">
                            {msg.suggested_next_steps.map((step, sIdx) => (
                              <button
                                key={sIdx}
                                type="button"
                                onClick={() => handleSendMessage(step)}
                                className="text-left text-xs px-3 py-1.5 rounded-xl bg-[#F7F8F4] hover:bg-[#EBF2EB] border border-[#E6EAE4] hover:border-[#347A50] text-[#183D2B] font-medium transition-colors flex items-center gap-1.5"
                              >
                                <span>{step}</span>
                                <ArrowRight className="w-3 h-3 text-[#347A50]" />
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Expandable Agent Telemetry Accordion */}
                      {msg.agent_telemetry && msg.agent_telemetry.length > 0 && (
                        <div className="pt-2 border-t border-[#F1F4EE]">
                          <button
                            type="button"
                            onClick={() => toggleTelemetry(msgKey)}
                            className="flex items-center justify-between w-full text-xs font-bold text-[#78837B] hover:text-[#202B24] transition-colors py-1"
                          >
                            <span className="flex items-center gap-1.5">
                              <Cpu className="w-3.5 h-3.5 text-[#347A50]" />
                              <span>7-Agent Telemetry & Execution Trace</span>
                            </span>
                            {isExpanded ? (
                              <ChevronUp className="w-4 h-4" />
                            ) : (
                              <ChevronDown className="w-4 h-4" />
                            )}
                          </button>

                          {isExpanded && (
                            <div className="mt-3 p-3 rounded-2xl bg-[#F7F8F4] border border-[#E6EAE4] space-y-2 animate-in fade-in">
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                {msg.agent_telemetry.map((ar, arIdx) => (
                                  <div
                                    key={arIdx}
                                    className="p-2.5 rounded-xl bg-white border border-[#E6EAE4] text-xs space-y-1.5"
                                  >
                                    <div className="flex items-center justify-between gap-2">
                                      <div className="min-w-0">
                                        <p className="font-bold text-[#202B24] truncate">
                                          {ar.agent_id}
                                        </p>
                                        <p className="text-[10px] text-[#78837B]">
                                          {ar.duration_ms}ms • {ar.model_id || 'heuristic'}
                                        </p>
                                      </div>
                                      <div>{getAgentStatusBadge(ar.status)}</div>
                                    </div>
                                    {ar.summary && (
                                      <p
                                        className="text-[11px] text-[#556057] bg-[#F7F8F4] p-1.5 rounded-lg border border-[#E6EAE4]/60 leading-relaxed break-words"
                                        data-testid={`telemetry-summary-${ar.agent_id.toLowerCase()}`}
                                      >
                                        {ar.summary}
                                      </p>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}

          {/* ACTIVE GENERATION / 7 AGENTS STEP INDICATOR */}
          {isSending && (
            <div className="flex justify-start gap-3 max-w-2xl mr-auto animate-in fade-in">
              <div className="w-8 h-8 rounded-full bg-[#347A50] text-white flex items-center justify-center shrink-0">
                <Sprout className="w-4 h-4 animate-pulse text-[#DCE9DC]" />
              </div>

              <div className="bg-white border border-[#E6EAE4] rounded-3xl p-5 shadow-xs space-y-3 flex-1">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="w-4 h-4 border-2 border-[#347A50] border-t-transparent rounded-full animate-spin" />
                    <span className="text-xs font-bold text-[#202B24]">
                      7 Agricultural Agents Collaborating...
                    </span>
                  </div>
                  <span className="text-[11px] text-[#78837B]">Multi-Agent Graph</span>
                </div>

                <div className="space-y-1.5">
                  {[
                    'Intake & Context Resolution',
                    'Open-Meteo Agrometeorology',
                    'ICAR Agronomic Science',
                    'Agmarknet Mandi Rates',
                    'Government Schemes',
                    'Risk Alert Screening',
                    'Coordinator Synthesis',
                  ].map((stepName, sIdx) => {
                    const isDone = sendStep > sIdx;
                    const isCurrent = sendStep === sIdx;
                    return (
                      <div key={sIdx} className="flex items-center gap-2 text-[11px]">
                        <span
                          className={`w-3.5 h-3.5 rounded-full flex items-center justify-center text-[8px] font-bold ${
                            isDone
                              ? 'bg-[#347A50] text-white'
                              : isCurrent
                              ? 'bg-amber-100 text-amber-800 border border-amber-400'
                              : 'bg-slate-100 text-slate-400'
                          }`}
                        >
                          {isDone ? '✓' : sIdx + 1}
                        </span>
                        <span
                          className={`truncate ${
                            isDone || isCurrent ? 'font-semibold text-[#202B24]' : 'text-[#78837B]'
                          }`}
                        >
                          {stepName}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* BOTTOM CONTEXT BAR: Active Confirmed Parameters */}
        <div className="px-6 py-2 bg-[#F7F8F4] border-t border-[#E6EAE4] flex items-center justify-between gap-3 text-xs text-[#78837B]">
          <div className="flex items-center gap-3 overflow-x-auto py-1">
            <span className="font-bold text-[#202B24] shrink-0 text-[11px] uppercase tracking-wider">
              Active Context:
            </span>
            <span className="px-2 py-0.5 rounded-md bg-white border border-[#E6EAE4] text-[#183D2B] font-medium shrink-0">
              🌾 Crop: {activeConversation?.context?.crop || overrideCrop || 'Auto-detecting'}
            </span>
            <span className="px-2 py-0.5 rounded-md bg-white border border-[#E6EAE4] text-slate-700 font-medium shrink-0">
              📍 Location: {activeConversation?.context?.location || overrideLocation || 'Auto-detecting'}
            </span>
            <span className="px-2 py-0.5 rounded-md bg-white border border-[#E6EAE4] text-slate-700 font-medium shrink-0">
              🌱 Stage: {activeConversation?.context?.growth_stage || overrideStage || 'Not set'}
            </span>
          </div>

          <button
            type="button"
            onClick={() => setShowParamDrawer((p) => !p)}
            className="text-[11px] font-bold text-[#347A50] hover:underline shrink-0"
          >
            {showParamDrawer ? 'Hide Details' : 'Edit Context'}
          </button>
        </div>

        {/* MESSAGE COMPOSER */}
        <div className="p-4 sm:p-5 border-t border-[#E6EAE4] bg-white">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSendMessage();
            }}
            className="flex items-end gap-3 max-w-4xl mx-auto"
          >
            <div className="flex-1 relative">
              <textarea
                ref={textareaRef}
                rows={2}
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about crop health, weather forecasts, mandi prices, or government schemes..."
                className="w-full resize-none p-3.5 pr-10 rounded-2xl border border-[#D5DCD5] focus:border-[#347A50] focus:ring-2 focus:ring-[#347A50]/20 text-sm text-[#202B24] placeholder-[#78837B] outline-hidden leading-relaxed shadow-inner"
                disabled={isSending}
              />
            </div>

            <Button
              type="submit"
              variant="primary"
              size="lg"
              disabled={isSending || !inputText.trim()}
              icon={Send}
              className="rounded-2xl h-[52px] px-5 shrink-0"
            >
              {isSending ? 'Sending...' : 'Send'}
            </Button>
          </form>
          <div className="text-center mt-2">
            <span className="text-[11px] text-[#78837B]">
              Press <strong>Enter</strong> to send • <strong>Shift + Enter</strong> for a new line • Powered by Microsoft Agent Framework
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ConversationalAdvisoryPage;
