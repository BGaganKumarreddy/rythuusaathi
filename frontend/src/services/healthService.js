import apiClient from './apiClient';

export const healthService = {
  async getHealth() {
    const res = await apiClient.get('/health');
    return res.data;
  },
};
