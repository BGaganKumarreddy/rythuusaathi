import React, { useState } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { Sprout, Lock, User, Building, ArrowRight } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { Input } from '../../components/ui/Input';
import { Button } from '../../components/ui/Button';

export const LoginPage = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [tenantId, setTenantId] = useState('default');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const { login } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();

  const from = location.state?.from?.pathname || '/';

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError('Please enter your username and password');
      return;
    }

    setError('');
    setIsLoading(true);

    try {
      await login({
        username: username.trim(),
        password,
        tenant_id: tenantId.trim() || 'default',
      });
      toast.success('Successfully authenticated', 'Welcome back');
      navigate(from, { replace: true });
    } catch (err) {
      setError(err?.message || 'Invalid username, password, or tenant ID');
      toast.error(err?.message || 'Authentication failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#F7F8F4] flex">
      {/* Left Column: Form */}
      <div className="flex-1 flex flex-col justify-center px-6 sm:px-12 lg:px-20 xl:px-24 py-12 bg-white max-w-xl">
        <div className="w-full max-w-sm mx-auto">
          {/* Logo & Brand */}
          <div className="flex items-center gap-3 mb-8">
            <div className="w-10 h-10 rounded-xl bg-[#183D2B] text-white flex items-center justify-center shadow-md">
              <Sprout className="w-6 h-6 text-[#DCE9DC]" />
            </div>
            <div>
              <h1 className="font-bold text-xl text-[#202B24] tracking-tight">RythuSaathi</h1>
              <p className="text-xs text-[#78837B]">Intelligent Farming Companion</p>
            </div>
          </div>

          <div className="mb-8">
            <h2 className="text-2xl font-bold text-[#202B24] tracking-tight">Welcome back</h2>
            <p className="text-sm text-[#78837B] mt-1">
              Enter your credentials to access your agricultural advisory dashboard
            </p>
          </div>

          {error && (
            <div className="mb-5 p-3.5 rounded-xl bg-red-50 border border-red-200 text-xs text-red-700 leading-relaxed">
              {error}
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Username"
              id="username"
              placeholder="e.g. ramesh_farmer"
              icon={User}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus
            />

            <Input
              label="Password"
              id="password"
              type="password"
              placeholder="••••••••••••"
              icon={Lock}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />

            <Input
              label="Tenant ID"
              id="tenant_id"
              placeholder="default"
              icon={Building}
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
              helperText="Default tenant is 'default' for personal accounts"
            />

            <div className="pt-2">
              <Button
                type="submit"
                variant="primary"
                size="md"
                className="w-full"
                isLoading={isLoading}
              >
                Sign In
              </Button>
            </div>
          </form>

          <div className="mt-8 pt-6 border-t border-[#E6EAE4] text-center">
            <p className="text-xs text-[#78837B]">
              Don&apos;t have an account?{' '}
              <Link to="/register" className="font-semibold text-[#183D2B] hover:underline">
                Create an account
              </Link>
            </p>
          </div>
        </div>
      </div>

      {/* Right Column: Hero Graphic (matching design reference 8) */}
      <div className="hidden lg:flex flex-1 bg-[#DCE9DC] relative overflow-hidden items-center justify-center p-12">
        <div className="absolute inset-0 bg-radial from-[#DCE9DC] via-[#cce0cc] to-[#b8d4b8] opacity-80" />
        <div className="relative z-10 max-w-lg text-[#183D2B] space-y-6">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/70 border border-[#183D2B]/10 text-xs font-semibold backdrop-blur-xs">
            <span className="w-2 h-2 rounded-full bg-[#347A50]" />
            Seven Specialized AI Agents
          </div>
          <h2 className="text-3xl xl:text-4xl font-extrabold tracking-tight leading-tight">
            Scientific, evidence-backed intelligence for every acre.
          </h2>
          <p className="text-sm text-[#183D2B]/80 leading-relaxed">
            RythuSaathi eliminates hallucinations by cross-verifying weather observations, ICAR agronomic research, daily APMC mandi rates, and central welfare schemes.
          </p>

          <div className="grid grid-cols-2 gap-4 pt-4">
            <div className="bg-white/80 backdrop-blur-xs p-4 rounded-xl border border-white shadow-xs">
              <p className="text-xl font-bold text-[#183D2B]">100%</p>
              <p className="text-xs text-[#183D2B]/75 mt-0.5">Grounded Evidence RAG</p>
            </div>
            <div className="bg-white/80 backdrop-blur-xs p-4 rounded-xl border border-white shadow-xs">
              <p className="text-xl font-bold text-[#183D2B]">AES-256</p>
              <p className="text-xs text-[#183D2B]/75 mt-0.5">Zero-Knowledge BYOK</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
