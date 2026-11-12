import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { configurationService } from '../services/configurationService';

const ConfigContext = createContext(null);

export const ConfigProvider = ({ children }) => {
  const [crops, setCrops] = useState([]);
  const [locations, setLocations] = useState([]);
  const [providers, setProviders] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadConfig = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [cropsData, locationsData, providersData] = await Promise.all([
        configurationService.getCrops(),
        configurationService.getLocations(),
        configurationService.getProviders(),
      ]);
      setCrops(cropsData || []);
      setLocations(locationsData || []);
      setProviders(providersData || []);
    } catch (err) {
      setError(err?.message || 'Failed to load configuration catalogs');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const value = {
    crops,
    locations,
    providers,
    isLoading,
    error,
    reload: loadConfig,
  };

  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>;
};

export const useConfig = () => {
  const context = useContext(ConfigContext);
  if (!context) {
    throw new Error('useConfig must be used within a ConfigProvider');
  }
  return context;
};

