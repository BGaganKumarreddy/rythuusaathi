import apiClient from './apiClient';

export const advisoryService = {
  async createAdvisory({ query, crop, location, growth_stage, variety, mode, credential_id }) {
    const res = await apiClient.post('/advisories', {
      query,
      crop: crop || undefined,
      location: location || undefined,
      growth_stage: growth_stage || undefined,
      variety: variety || undefined,
      mode: mode || 'deterministic',
      credential_id: credential_id || undefined,
    });
    return res.data;
  },

  async getAdvisory(advisoryId) {
    const res = await apiClient.get(`/advisories/${encodeURIComponent(advisoryId)}`);
    return res.data;
  },

  async getAdvisoryTrace(advisoryId) {
    const res = await apiClient.get(`/advisories/${encodeURIComponent(advisoryId)}/trace`);
    return res.data;
  },

  async listAdvisories({ limit = 20, offset = 0, crop } = {}) {
    const params = { limit, offset };
    if (crop) params.crop = crop;
    const res = await apiClient.get('/advisories', { params });
    return res.data;
  },
};
