import React from 'react';
import { Loader2 } from 'lucide-react';

export const Button = ({
  children,
  variant = 'primary',
  size = 'md',
  isLoading = false,
  disabled = false,
  icon: Icon,
  className = '',
  type = 'button',
  onClick,
  ...props
}) => {
  const baseStyles = 'inline-flex items-center justify-center font-medium rounded-xl transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-60 disabled:cursor-not-allowed select-none';

  const variants = {
    primary: 'bg-[#183D2B] hover:bg-[#23543b] active:bg-[#10291d] text-white focus:ring-[#183D2B] shadow-sm',
    secondary: 'bg-[#347A50] hover:bg-[#429563] text-white focus:ring-[#347A50] shadow-sm',
    sage: 'bg-[#DCE9DC] hover:bg-[#c2e4cf] text-[#183D2B] focus:ring-[#347A50]',
    outline: 'border border-[#E6EAE4] hover:border-[#347A50] hover:bg-[#F7F8F4] text-[#202B24] focus:ring-[#347A50] bg-white',
    ghost: 'hover:bg-[#EAF2EA] text-[#202B24] focus:ring-[#347A50]',
    danger: 'bg-[#D9534F] hover:bg-red-700 text-white focus:ring-red-600 shadow-sm',
  };

  const sizes = {
    sm: 'text-xs px-3 py-1.5 gap-1.5',
    md: 'text-sm px-4 py-2.5 gap-2',
    lg: 'text-base px-6 py-3 gap-2.5',
  };

  return (
    <button
      type={type}
      disabled={disabled || isLoading}
      onClick={onClick}
      className={`${baseStyles} ${variants[variant]} ${sizes[size]} ${className}`}
      {...props}
    >
      {isLoading ? (
        <Loader2 className="w-4 h-4 animate-spin shrink-0" />
      ) : Icon ? (
        <Icon className="w-4 h-4 shrink-0" />
      ) : null}
      <span>{children}</span>
    </button>
  );
};

