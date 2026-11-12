import axios from 'axios';
import { API_BASE_URL } from '../utils/constants';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 45000, // 45 seconds for LLM workflow synthesis
});

let tokenGetter = () => localStorage.getItem('rythusaathi_token');
let onUnauthorizedCallback = null;

export const setTokenGetter = (fn) => {
  tokenGetter = fn;
};

export const setOnUnauthorized = (fn) => {
  onUnauthorizedCallback = fn;
};

// Request interceptor: attach bearer token
apiClient.interceptors.request.use(
  (config) => {
    const token = tokenGetter ? tokenGetter() : null;
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor: format errors and handle 401
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response ? error.response.status : null;
    const data = error.response ? error.response.data : null;

    if (status === 401 && onUnauthorizedCallback) {
      onUnauthorizedCallback();
    }

    // Format uniform error object
    const formattedError = {
      status,
      code: data?.code || (status ? `http_${status}` : 'network_error'),
      message: data?.message || error.message || 'An unexpected error occurred',
      details: data?.details || {},
      raw: error,
    };

    return Promise.reject(formattedError);
  }
);

export default apiClient;
