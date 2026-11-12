import apiClient from './apiClient';

export const configurationService = {
  async getCrops() {
    const res = await apiClient.get('/configuration/crops');
    return res.data;
  },

  async getLocations() {
    const res = await apiClient.get('/configuration/locations');
    return res.data;
  },

  async getProviders() {
    const res = await apiClient.get('/configuration/providers');
    return res.data;
  },
};

