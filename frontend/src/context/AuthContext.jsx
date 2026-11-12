import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { authService } from '../services/authService';
import { setTokenGetter, setOnUnauthorized } from '../services/apiClient';

const AuthContext = createContext(null);

const TOKEN_KEY = 'rythusaathi_auth_token';

export const AuthProvider = ({ children }) => {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  // Wire up apiClient token getter
  useEffect(() => {
    setTokenGetter(() => localStorage.getItem(TOKEN_KEY));
  }, []);

  const logout = useCallback(async () => {
    try {
      if (token) {
        await authService.logout();
      }
    } finally {
      localStorage.removeItem(TOKEN_KEY);
      setToken(null);
      setUser(null);
    }
  }, [token]);

  // Wire 401 callback
  useEffect(() => {
    setOnUnauthorized(() => {
      logout();
    });
  }, [logout]);

  // Fetch current user if token exists on boot
  useEffect(() => {
    let isMounted = true;

    async function initAuth() {
      const storedToken = localStorage.getItem(TOKEN_KEY);
      if (!storedToken) {
        if (isMounted) setIsLoading(false);
        return;
      }

      try {
        const userData = await authService.getMe();
        if (isMounted) {
          setUser(userData);
          setToken(storedToken);
        }
      } catch (err) {
        // Token invalid or expired
        if (isMounted) {
          localStorage.removeItem(TOKEN_KEY);
          setToken(null);
          setUser(null);
        }
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }

    initAuth();

    return () => {
      isMounted = false;
    };
  }, []);

  const login = async ({ username, password, tenant_id }) => {
    const data = await authService.login({ username, password, tenant_id });
    localStorage.setItem(TOKEN_KEY, data.access_token);
    setToken(data.access_token);
    setUser({
      user_id: data.user_id,
      tenant_id: data.tenant_id,
      roles: data.roles,
      expires_at: data.expires_at,
    });
    return data;
  };

  const register = async ({ username, password, tenant_id, tenant_name, role }) => {
    const data = await authService.register({
      username,
      password,
      tenant_id,
      tenant_name,
      role,
    });
    return data;
  };

  const refreshUser = async () => {
    try {
      const userData = await authService.getMe();
      setUser(userData);
      return userData;
    } catch {
      logout();
      return null;
    }
  };

  const isAdmin = !!(user?.roles && (user.roles.includes('tenant_admin') || user.roles.includes('admin')));

  const value = {
    token,
    user,
    isAdmin,
    isAuthenticated: !!token && !!user,
    isLoading,
    login,
    register,
    logout,
    refreshUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

