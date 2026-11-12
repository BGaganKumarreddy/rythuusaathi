import React from 'react';
import { AlertCircle, AlertTriangle, CheckCircle2, Info } from 'lucide-react';

export const Skeleton = ({ className = '', rounded = 'rounded-xl' }) => {
  return (
    <div
      className={`animate-pulse bg-slate-200/80 ${rounded} ${className}`}
    />
  );
};

export const CardSkeleton = () => {
  return (
    <div className="bg-white rounded-2xl border border-[#E6EAE4] p-6 space-y-4">
      <div className="flex items-center justify-between">
        <Skeleton className="h-6 w-1/3" />
        <Skeleton className="h-6 w-16" rounded="rounded-lg" />
      </div>
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-1/2" />
      <div className="pt-2 flex gap-2">
        <Skeleton className="h-8 w-24" />
        <Skeleton className="h-8 w-24" />
      </div>
    </div>
  );
};

export const EmptyState = ({
  icon: Icon,
  title,
  description,
  action,
  className = '',
}) => {
  return (
    <div
      className={`flex flex-col items-center justify-center p-8 sm:p-12 text-center rounded-2xl border border-dashed border-[#E6EAE4] bg-[#F7F8F4]/50 ${className}`}
    >
      {Icon && (
        <div className="w-12 h-12 rounded-2xl bg-[#DCE9DC] text-[#183D2B] flex items-center justify-center mb-4">
          <Icon className="w-6 h-6" />
        </div>
      )}
      <h3 className="text-base font-semibold text-[#202B24] mb-1">{title}</h3>
      {description && (
        <p className="text-sm text-[#78837B] max-w-sm mb-5 leading-relaxed">
          {description}
        </p>
      )}
      {action && <div>{action}</div>}
    </div>
  );
};

export const Alert = ({
  type = 'info',
  title,
  children,
  className = '',
}) => {
  const styles = {
    info: {
      bg: 'bg-sky-50',
      border: 'border-sky-200',
      text: 'text-sky-800',
      icon: <Info className="w-5 h-5 text-sky-600 shrink-0" />,
    },
    warning: {
      bg: 'bg-amber-50',
      border: 'border-amber-200',
      text: 'text-amber-800',
      icon: <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0" />,
    },
    error: {
      bg: 'bg-red-50',
      border: 'border-red-200',
      text: 'text-red-800',
      icon: <AlertCircle className="w-5 h-5 text-red-600 shrink-0" />,
    },
    success: {
      bg: 'bg-emerald-50',
      border: 'border-emerald-200',
      text: 'text-emerald-800',
      icon: <CheckCircle2 className="w-5 h-5 text-emerald-600 shrink-0" />,
    },
  };

  const current = styles[type] || styles.info;

  return (
    <div
      className={`flex items-start gap-3 p-4 rounded-xl border ${current.bg} ${current.border} ${className}`}
      role="alert"
    >
      {current.icon}
      <div className="flex-1 text-sm leading-relaxed">
        {title && <h4 className={`font-semibold mb-0.5 ${current.text}`}>{title}</h4>}
        <div className={current.text}>{children}</div>
      </div>
    </div>
  );
};
