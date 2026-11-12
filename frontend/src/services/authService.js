import apiClient from './apiClient';

export const authService = {
  async register({ username, password, tenant_id, tenant_name, role }) {
    const res = await apiClient.post('/auth/register', {
      username,
      password,
      tenant_id: tenant_id || 'default',
      tenant_name: tenant_name || undefined,
      role: role || 'member',
    });
    return res.data;
  },

  async login({ username, password, tenant_id }) {
    const res = await apiClient.post('/auth/login', {
      username,
      password,
      tenant_id: tenant_id || 'default',
    });
    return res.data;
  },

  async logout() {
    try {
      const res = await apiClient.post('/auth/logout');
      return res.data;
    } catch {
      // Ignore network errors on logout
      return { success: true };
    }
  },

  async getMe() {
    const res = await apiClient.get('/auth/me');
    return res.data;
  },
};

