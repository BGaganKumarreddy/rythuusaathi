import { apiClient } from './apiClient';

export const adminService = {
  getDashboard: async () => {
    return apiClient.get('/admin/dashboard');
  },
  getUsers: async () => {
    return apiClient.get('/admin/users');
  },
  getAgents: async () => {
    return apiClient.get('/admin/agents');
  },
  getSystem: async () => {
    return apiClient.get('/admin/system');
  },
};

