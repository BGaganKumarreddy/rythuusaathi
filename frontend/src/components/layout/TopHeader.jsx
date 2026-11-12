import React, { useState, useEffect } from 'react';
import { Menu, Activity, ShieldCheck } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { healthService } from '../../services/healthService';
import { Badge } from '../ui/Badge';

export const TopHeader = ({ onMenuClick, title, breadcrumbs = [] }) => {
  const { user } = useAuth();
  const [health, setHealth] = useState({ status: 'checking', agents: 7 });

  useEffect(() => {
    let isMounted = true;
    healthService
      .getHealth()
      .then((data) => {
        if (isMounted) setHealth(data);
      })
      .catch(() => {
        if (isMounted) setHealth({ status: 'offline', agents: 7 });
      });

    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <header className="sticky top-0 z-20 bg-white/90 backdrop-blur-md border-b border-[#E6EAE4] px-4 sm:px-6 lg:px-8 py-3.5 flex items-center justify-between transition-all">
      <div className="flex items-center gap-3 min-w-0">
        <button
          onClick={onMenuClick}
          className="lg:hidden p-2 rounded-xl text-[#202B24] hover:bg-[#F7F8F4] border border-[#E6EAE4] transition-colors"
          aria-label="Open sidebar menu"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="min-w-0">
          {breadcrumbs.length > 0 && (
            <div className="flex items-center gap-1.5 text-xs text-[#78837B] mb-0.5 truncate">
              {breadcrumbs.map((crumb, idx) => (
                <React.Fragment key={crumb.label}>
                  {idx > 0 && <span>/</span>}
                  <span>{crumb.label}</span>
                </React.Fragment>
              ))}
            </div>
          )}
          <h2 className="text-lg sm:text-xl font-bold text-[#202B24] tracking-tight truncate">
            {title || 'Agricultural Advisory'}
          </h2>
        </div>
      </div>

      <div className="flex items-center gap-3 shrink-0">
        {/* System Health Status Indicator */}
        <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-xl border border-[#E6EAE4] bg-[#F7F8F4]">
          <Activity
            className={`w-4 h-4 ${
              health.status === 'healthy' ? 'text-emerald-600' : 'text-amber-500'
            }`}
          />
          <span className="text-xs font-medium text-[#202B24]">
            {health.status === 'healthy' ? '7 Agents Active' : 'Checking Agents'}
          </span>
          <span
            className={`w-2 h-2 rounded-full ${
              health.status === 'healthy' ? 'bg-emerald-500 animate-pulse' : 'bg-amber-400'
            }`}
          />
        </div>

        {/* User avatar badge */}
        <div className="flex items-center gap-2 pl-2">
          <div className="w-9 h-9 rounded-xl bg-[#DCE9DC] text-[#183D2B] flex items-center justify-center font-semibold text-sm border border-[#c2e4cf]">
            {user?.user_id ? user.user_id.slice(0, 2).toUpperCase() : 'RS'}
          </div>
        </div>
      </div>
    </header>
  );
};
