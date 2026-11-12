import apiClient from './apiClient';

export const historyService = {
  async getHistory({ limit = 20, offset = 0, crop } = {}) {
    const params = { limit, offset };
    if (crop) params.crop = crop;
    const res = await apiClient.get('/history', { params });
    return res.data;
  },
};

