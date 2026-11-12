import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Sprout, Lock, User, Building, Shield } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { Input, Select } from '../../components/ui/Input';
import { Button } from '../../components/ui/Button';

export const RegisterPage = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [tenantId, setTenantId] = useState('default');
  const [tenantName, setTenantName] = useState('');
  const [role, setRole] = useState('member');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');

  const { register, login } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setError('Please provide a username and password');
      return;
    }
    if (password.length < 12) {
      setError('Password must contain at least 12 characters');
      return;
    }

    setError('');
    setIsLoading(true);

    try {
      await register({
        username: username.trim(),
        password,
        tenant_id: tenantId.trim() || 'default',
        tenant_name: tenantName.trim() || undefined,
        role,
      });

      toast.success('Registration successful! Logging in...', 'Account Created');

      // Automatically sign in upon registration
      try {
        await login({
          username: username.trim(),
          password,
          tenant_id: tenantId.trim() || 'default',
        });
        navigate('/');
      } catch {
        navigate('/login');
      }
    } catch (err) {
      setError(err?.message || 'Registration failed');
      toast.error(err?.message || 'Registration failed');
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

          <div className="mb-6">
            <h2 className="text-2xl font-bold text-[#202B24] tracking-tight">Create your account</h2>
            <p className="text-sm text-[#78837B] mt-1">
              Join the multi-agent agricultural advisory network
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
              placeholder="e.g. kiran_patel"
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
              placeholder="Minimum 12 characters"
              icon={Lock}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              helperText="Must be at least 12 characters long"
              required
            />

            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Tenant ID"
                id="tenant_id"
                placeholder="default"
                icon={Building}
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                required
              />

              <Select
                label="Role"
                id="role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                options={[
                  { value: 'member', label: 'Member' },
                  { value: 'tenant_admin', label: 'Tenant Admin' },
                ]}
                placeholder=""
              />
            </div>

            <Input
              label="Tenant Name (Optional)"
              id="tenant_name"
              placeholder="e.g. Adilabad Cooperative"
              value={tenantName}
              onChange={(e) => setTenantName(e.target.value)}
              helperText="Only used when creating a brand new tenant"
            />

            <div className="pt-2">
              <Button
                type="submit"
                variant="primary"
                size="md"
                className="w-full"
                isLoading={isLoading}
              >
                Create Account
              </Button>
            </div>
          </form>

          <div className="mt-8 pt-6 border-t border-[#E6EAE4] text-center">
            <p className="text-xs text-[#78837B]">
              Already have an account?{' '}
              <Link to="/login" className="font-semibold text-[#183D2B] hover:underline">
                Sign in
              </Link>
            </p>
          </div>
        </div>
      </div>

      {/* Right Column: Hero Graphic */}
      <div className="hidden lg:flex flex-1 bg-[#DCE9DC] relative overflow-hidden items-center justify-center p-12">
        <div className="absolute inset-0 bg-radial from-[#DCE9DC] via-[#cce0cc] to-[#b8d4b8] opacity-80" />
        <div className="relative z-10 max-w-lg text-[#183D2B] space-y-6">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/70 border border-[#183D2B]/10 text-xs font-semibold backdrop-blur-xs">
            <Shield className="w-3.5 h-3.5 text-[#347A50]" />
            Local-First & Encrypted
          </div>
          <h2 className="text-3xl xl:text-4xl font-extrabold tracking-tight leading-tight">
            Multi-Tenant Isolation & Zero Secret Leakage.
          </h2>
          <p className="text-sm text-[#183D2B]/80 leading-relaxed">
            Your agricultural consultations and credentials stay strictly isolated to your user profile and cooperative tenant under AES-256-GCM envelope encryption.
          </p>
        </div>
      </div>
    </div>
  );
};

