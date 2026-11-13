import apiClient from './apiClient';

export const conversationService = {
  /**
   * Create a new conversation session, optionally sending an initial message.
   */
  async createConversation({
    title,
    initial_message,
    crop,
    location,
    growth_stage,
    variety,
    mode = 'deterministic',
  } = {}) {
    const payload = {
      title: title || undefined,
      initial_message: initial_message || undefined,
      crop: crop || undefined,
      location: location || undefined,
      growth_stage: growth_stage || undefined,
      variety: variety || undefined,
      mode: mode || 'deterministic',
    };
    const res = await apiClient.post('/conversations', payload);
    return res.data;
  },

  /**
   * List conversations for the authenticated user with tenant isolation.
   */
  async listConversations({ limit = 20, offset = 0 } = {}) {
    const params = { limit, offset };
    const res = await apiClient.get('/conversations', { params });
    return res.data;
  },

  /**
   * Retrieve an owned conversation and its messages by ID.
   */
  async getConversation(conversationId) {
    const res = await apiClient.get(`/conversations/${encodeURIComponent(conversationId)}`);
    return res.data;
  },

  /**
   * Send a follow-up user message in an existing conversation thread.
   */
  async sendMessage(
    conversationId,
    {
      content,
      crop,
      location,
      growth_stage,
      variety,
      mode = 'deterministic',
    }
  ) {
    const payload = {
      content,
      crop: crop || undefined,
      location: location || undefined,
      growth_stage: growth_stage || undefined,
      variety: variety || undefined,
      mode: mode || 'deterministic',
    };
    const res = await apiClient.post(
      `/conversations/${encodeURIComponent(conversationId)}/messages`,
      payload
    );
    return res.data;
  },

  /**
   * Delete an owned conversation.
   */
  async deleteConversation(conversationId) {
    const res = await apiClient.delete(`/conversations/${encodeURIComponent(conversationId)}`);
    return res.data;
  },
};

export default conversationService;
