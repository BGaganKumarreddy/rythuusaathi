import React from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ConfigProvider } from './context/ConfigContext';
import { ToastProvider } from './context/ToastContext';
import { AppLayout } from './components/layout/AppLayout';

// Pages
import { LoginPage } from './pages/auth/LoginPage';
import { RegisterPage } from './pages/auth/RegisterPage';
import { DashboardPage } from './pages/dashboard/DashboardPage';
import { NewAdvisoryPage } from './pages/advisory/NewAdvisoryPage';
import { AdvisoryResultsPage } from './pages/advisory/AdvisoryResultsPage';
import { HistoryPage } from './pages/history/HistoryPage';
import { AgentActivityPage } from './pages/advisory/AgentActivityPage';
import { WeatherViewPage } from './pages/advisory/WeatherViewPage';
import { MarketViewPage } from './pages/advisory/MarketViewPage';
import { SchemesViewPage } from './pages/advisory/SchemesViewPage';
import { RiskAlertsViewPage } from './pages/advisory/RiskAlertsViewPage';
import { CredentialsPage } from './pages/credentials/CredentialsPage';
import { SettingsPage } from './pages/settings/SettingsPage';

// Protected Route Guard
const ProtectedRoute = ({ children }) => {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div className="min-h-screen bg-[#F7F8F4] flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="w-10 h-10 border-3 border-[#DCE9DC] border-t-[#183D2B] rounded-full animate-spin" />
          <p className="text-xs font-semibold text-[#183D2B]">Initializing RythuSaathi...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return children;
};

// Public Route Guard (redirects logged-in users away from /login & /register)
const PublicRoute = ({ children }) => {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) return null;
  if (isAuthenticated) return <Navigate to="/" replace />;

  return children;
};

export const App = () => {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ConfigProvider>
          <ToastProvider>
            <Routes>
              {/* Public Auth Routes */}
              <Route
                path="/login"
                element={
                  <PublicRoute>
                    <LoginPage />
                  </PublicRoute>
                }
              />
              <Route
                path="/register"
                element={
                  <PublicRoute>
                    <RegisterPage />
                  </PublicRoute>
                }
              />

              {/* Protected Application Routes */}
              <Route
                path="/"
                element={
                  <ProtectedRoute>
                    <AppLayout title="Agricultural Dashboard" />
                  </ProtectedRoute>
                }
              >
                <Route index element={<DashboardPage />} />
                <Route path="advisories/new" element={<NewAdvisoryPage />} />
                <Route path="advisories/:advisoryId" element={<AdvisoryResultsPage />} />
                <Route path="advisories" element={<HistoryPage />} />
                <Route path="agent-activity" element={<AgentActivityPage />} />
                <Route path="weather" element={<WeatherViewPage />} />
                <Route path="market" element={<MarketViewPage />} />
                <Route path="schemes" element={<SchemesViewPage />} />
                <Route path="risk-alerts" element={<RiskAlertsViewPage />} />
                <Route path="credentials" element={<CredentialsPage />} />
                <Route path="settings" element={<SettingsPage />} />
              </Route>

              {/* Fallback */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </ToastProvider>
        </ConfigProvider>
      </AuthProvider>
    </BrowserRouter>
  );
};

export default App;
