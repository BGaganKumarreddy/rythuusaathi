import React from 'react';

export const Badge = ({
  children,
  variant = 'neutral',
  size = 'md',
  dot = false,
  className = '',
}) => {
  const variants = {
    completed: 'bg-emerald-50 text-emerald-800 border-emerald-200',
    partial: 'bg-amber-50 text-amber-800 border-amber-200',
    unavailable: 'bg-slate-100 text-slate-700 border-slate-200',
    failed: 'bg-red-50 text-red-800 border-red-200',
    active: 'bg-[#DCE9DC] text-[#183D2B] border-[#c2e4cf]',
    forest: 'bg-[#183D2B] text-white border-transparent',
    neutral: 'bg-slate-50 text-slate-700 border-slate-200',
    critical: 'bg-red-100 text-red-800 border-red-300 font-semibold',
    high: 'bg-amber-100 text-amber-800 border-amber-300 font-semibold',
    medium: 'bg-yellow-50 text-yellow-800 border-yellow-200 font-medium',
    low: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  };

  const dots = {
    completed: 'bg-emerald-500',
    partial: 'bg-amber-500',
    unavailable: 'bg-slate-400',
    failed: 'bg-red-500',
    active: 'bg-[#347A50]',
    forest: 'bg-white',
    neutral: 'bg-slate-400',
    critical: 'bg-red-600',
    high: 'bg-amber-600',
    medium: 'bg-yellow-600',
    low: 'bg-emerald-500',
  };

  const sizes = {
    sm: 'text-[11px] px-2 py-0.5 rounded-md gap-1',
    md: 'text-xs px-2.5 py-1 rounded-lg gap-1.5',
    lg: 'text-sm px-3 py-1.5 rounded-xl gap-2',
  };

  const vKey = variant.toLowerCase();
  const currentVariant = variants[vKey] || variants.neutral;
  const currentDot = dots[vKey] || dots.neutral;

  return (
    <span
      className={`inline-flex items-center font-medium border leading-none shrink-0 ${currentVariant} ${sizes[size]} ${className}`}
    >
      {dot && <span className={`w-1.5 h-1.5 rounded-full ${currentDot}`} />}
      <span>{children}</span>
    </span>
  );
};

