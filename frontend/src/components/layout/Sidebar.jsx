import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  LayoutDashboard,
  Sparkles,
  FileText,
  CloudSun,
  TrendingUp,
  Landmark,
  AlertTriangle,
  KeyRound,
  Settings,
  LogOut,
  Sprout,
  X,
  Cpu,
  UserCheck,
  ShieldCheck,
  Users,
  Server,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { Badge } from '../ui/Badge';

export const Sidebar = ({ isOpen, onClose }) => {
  const { user, isAdmin, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const primaryNavItems = [
    { to: '/', label: 'Dashboard', icon: LayoutDashboard },
    { to: '/advisories/new', label: 'New Consultation', icon: Sparkles, highlight: true },
    { to: '/history', label: 'My Consultations', icon: FileText },
    { to: '/farm-profile', label: 'Farm Profile', icon: UserCheck },
    { to: '/settings', label: 'Account Settings', icon: Settings },
  ];

  const adminNavItems = [
    { to: '/admin', label: 'Admin Dashboard', icon: ShieldCheck },
    { to: '/admin/users', label: 'User Management', icon: Users },
    { to: '/admin/agents', label: 'Agent Activity & Telemetry', icon: Cpu },
    { to: '/admin/system', label: 'System Configuration', icon: Server },
  ];

  const sidebarContent = (
    <div className="flex flex-col h-full bg-[#183D2B] text-white">
      {/* Brand Header */}
      <div className="flex items-center justify-between px-6 py-6 border-b border-[#23543b]">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#347A50] flex items-center justify-center text-white shadow-md shadow-emerald-950/20">
            <Sprout className="w-6 h-6 text-[#DCE9DC]" />
          </div>
          <div>
            <h1 className="font-bold text-lg tracking-tight text-white leading-tight">
              RythuSaathi
            </h1>
            <p className="text-[11px] text-[#DCE9DC]/80 font-medium">
              Intelligent Farming Companion
            </p>
          </div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="lg:hidden p-1.5 rounded-lg text-emerald-200 hover:text-white hover:bg-[#23543b]"
          >
            <X className="w-5 h-5" />
          </button>
        )}
      </div>

      {/* Primary Action Button */}
      <div className="px-5 pt-5 pb-2">
        <NavLink
          to="/advisories/new"
          onClick={onClose}
          className="flex items-center justify-center gap-2 w-full py-3 px-4 rounded-xl bg-[#347A50] hover:bg-[#429563] text-white font-medium text-sm transition-all shadow-md shadow-emerald-950/30 group"
        >
          <Sparkles className="w-4 h-4 text-[#DCE9DC] group-hover:scale-110 transition-transform" />
          <span>New Consultation</span>
        </NavLink>
      </div>

      {/* Navigation List */}
      <nav className="flex-1 px-4 py-3 space-y-1 overflow-y-auto">
        <div className="space-y-1">
          {primaryNavItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                onClick={onClose}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-[#347A50] text-white shadow-xs'
                      : 'text-[#DCE9DC]/80 hover:bg-[#23543b]/60 hover:text-white'
                  }`
                }
              >
                <Icon className="w-4 h-4 shrink-0" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </div>

        {/* Administration Section: strictly hidden from farmer members */}
        {isAdmin && (
          <div className="pt-4 mt-4 border-t border-[#23543b]">
            <div className="px-3 pb-2 flex items-center justify-between">
              <span className="text-[11px] font-bold text-[#DCE9DC]/60 uppercase tracking-wider">
                Administration
              </span>
              <span className="text-[10px] bg-amber-900/60 text-amber-300 font-semibold px-1.5 py-0.5 rounded">
                Admin Only
              </span>
            </div>
            <div className="space-y-1">
              {adminNavItems.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    onClick={onClose}
                    className={({ isActive }) =>
                      `flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                        isActive
                          ? 'bg-[#347A50] text-white shadow-xs'
                          : 'text-[#DCE9DC]/80 hover:bg-[#23543b]/60 hover:text-white'
                      }`
                    }
                  >
                    <Icon className="w-4 h-4 shrink-0" />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          </div>
        )}
      </nav>

      {/* User / Tenant Info Bottom Card */}
      <div className="p-4 border-t border-[#23543b] bg-[#143324]">
        <div className="flex items-center justify-between mb-2">
          <div className="min-w-0 pr-2">
            <p className="text-xs font-semibold text-white truncate">
              {user?.user_id || 'Authenticated User'}
            </p>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span className="text-[11px] text-[#DCE9DC]/70 truncate">
                Tenant: {user?.tenant_id || 'default'}
              </span>
              {user?.roles?.includes('tenant_admin') && (
                <Badge variant="active" size="sm">Admin</Badge>
              )}
            </div>
          </div>
          <button
            onClick={handleLogout}
            title="Log out"
            className="p-2 rounded-lg text-emerald-200/70 hover:text-white hover:bg-[#23543b] transition-colors"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <>
      {/* Desktop Static Sidebar */}
      <aside className="hidden lg:flex lg:flex-col lg:w-64 lg:shrink-0 lg:fixed lg:inset-y-0 lg:z-30">
        {sidebarContent}
      </aside>

      {/* Mobile Slide-over Drawer */}
      {isOpen && (
        <div className="lg:hidden fixed inset-0 z-50 flex">
          <div
            className="fixed inset-0 bg-slate-900/60 backdrop-blur-xs transition-opacity animate-in fade-in"
            onClick={onClose}
          />
          <div className="relative flex flex-col w-72 max-w-xs h-full shadow-2xl animate-in slide-in-from-left duration-200">
            {sidebarContent}
          </div>
        </div>
      )}
    </>
  );
};

