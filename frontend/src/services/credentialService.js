import apiClient from './apiClient';

export const credentialService = {
  async listCredentials() {
    const res = await apiClient.get('/credentials');
    return res.data;
  },

  async createCredential({ provider_id, secret }) {
    const res = await apiClient.post('/credentials', {
      provider_id,
      secret,
    });
    return res.data;
  },

  async deleteCredential(credentialId) {
    const res = await apiClient.delete(`/credentials/${encodeURIComponent(credentialId)}`);
    return res.data;
  },
};
