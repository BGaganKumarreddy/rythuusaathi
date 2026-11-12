import React from 'react';

export const Card = ({
  children,
  className = '',
  title,
  subtitle,
  action,
  headerIcon: Icon,
  badge,
  padding = 'p-6',
  hover = false,
  onClick,
  ...props
}) => {
  return (
    <div
      onClick={onClick}
      className={`bg-white rounded-2xl border border-[#E6EAE4] shadow-xs ${
        hover ? 'hover:border-[#347A50]/40 hover:shadow-md transition-all duration-200 cursor-pointer' : ''
      } ${className}`}
      {...props}
    >
      {(title || action || Icon) && (
        <div className="flex items-center justify-between px-6 py-4.5 border-b border-[#E6EAE4]">
          <div className="flex items-center gap-3 min-w-0">
            {Icon && (
              <div className="w-8 h-8 rounded-lg bg-[#DCE9DC] text-[#183D2B] flex items-center justify-center shrink-0">
                <Icon className="w-4 h-4" />
              </div>
            )}
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="font-semibold text-[#202B24] text-base truncate">{title}</h3>
                {badge}
              </div>
              {subtitle && <p className="text-xs text-[#78837B] truncate mt-0.5">{subtitle}</p>}
            </div>
          </div>
          {action && <div className="shrink-0 ml-3">{action}</div>}
        </div>
      )}
      <div className={padding}>{children}</div>
    </div>
  );
};

