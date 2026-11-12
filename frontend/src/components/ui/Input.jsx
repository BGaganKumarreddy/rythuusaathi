import React, { forwardRef } from 'react';

export const Input = forwardRef(
  (
    {
      label,
      error,
      helperText,
      icon: Icon,
      className = '',
      id,
      type = 'text',
      required = false,
      ...props
    },
    ref
  ) => {
    const inputId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);

    return (
      <div className="w-full">
        {label && (
          <label htmlFor={inputId} className="block text-sm font-medium text-[#202B24] mb-1.5">
            {label}
            {required && <span className="text-red-500 ml-1">*</span>}
          </label>
        )}
        <div className="relative">
          {Icon && (
            <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-[#78837B]">
              <Icon className="w-4 h-4" />
            </div>
          )}
          <input
            id={inputId}
            ref={ref}
            type={type}
            required={required}
            className={`w-full rounded-xl border ${
              error ? 'border-red-400 focus:ring-red-400' : 'border-[#E6EAE4] focus:border-[#347A50] focus:ring-[#347A50]'
            } bg-white px-3.5 py-2.5 text-sm text-[#202B24] placeholder:text-[#78837B]/70 focus:outline-none focus:ring-2 focus:ring-offset-0 transition-all ${
              Icon ? 'pl-10' : ''
            } disabled:bg-[#F7F8F4] disabled:text-[#78837B] ${className}`}
            {...props}
          />
        </div>
        {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
        {helperText && !error && <p className="mt-1 text-xs text-[#78837B]">{helperText}</p>}
      </div>
    );
  }
);

Input.displayName = 'Input';

export const Select = forwardRef(
  (
    {
      label,
      error,
      helperText,
      icon: Icon,
      options = [],
      placeholder = 'Select an option',
      className = '',
      id,
      required = false,
      children,
      ...props
    },
    ref
  ) => {
    const selectId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);

    return (
      <div className="w-full">
        {label && (
          <label htmlFor={selectId} className="block text-sm font-medium text-[#202B24] mb-1.5">
            {label}
            {required && <span className="text-red-500 ml-1">*</span>}
          </label>
        )}
        <div className="relative">
          {Icon && (
            <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-[#78837B]">
              <Icon className="w-4 h-4" />
            </div>
          )}
          <select
            id={selectId}
            ref={ref}
            required={required}
            className={`w-full rounded-xl border ${
              error ? 'border-red-400 focus:ring-red-400' : 'border-[#E6EAE4] focus:border-[#347A50] focus:ring-[#347A50]'
            } bg-white px-3.5 py-2.5 text-sm text-[#202B24] focus:outline-none focus:ring-2 focus:ring-offset-0 transition-all appearance-none cursor-pointer ${
              Icon ? 'pl-10' : ''
            } disabled:bg-[#F7F8F4] disabled:text-[#78837B] ${className}`}
            {...props}
          >
            {placeholder && <option value="">{placeholder}</option>}
            {children ||
              options.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
          </select>
          <div className="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none text-[#78837B]">
            <svg className="w-4 h-4 fill-current" viewBox="0 0 20 20">
              <path
                fillRule="evenodd"
                d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </div>
        </div>
        {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
        {helperText && !error && <p className="mt-1 text-xs text-[#78837B]">{helperText}</p>}
      </div>
    );
  }
);

Select.displayName = 'Select';

export const Textarea = forwardRef(
  (
    {
      label,
      error,
      helperText,
      className = '',
      id,
      required = false,
      rows = 4,
      ...props
    },
    ref
  ) => {
    const areaId = id || (label ? label.toLowerCase().replace(/\s+/g, '-') : undefined);

    return (
      <div className="w-full">
        {label && (
          <label htmlFor={areaId} className="block text-sm font-medium text-[#202B24] mb-1.5">
            {label}
            {required && <span className="text-red-500 ml-1">*</span>}
          </label>
        )}
        <textarea
          id={areaId}
          ref={ref}
          required={required}
          rows={rows}
          className={`w-full rounded-xl border ${
            error ? 'border-red-400 focus:ring-red-400' : 'border-[#E6EAE4] focus:border-[#347A50] focus:ring-[#347A50]'
          } bg-white px-3.5 py-2.5 text-sm text-[#202B24] placeholder:text-[#78837B]/70 focus:outline-none focus:ring-2 focus:ring-offset-0 transition-all disabled:bg-[#F7F8F4] disabled:text-[#78837B] ${className}`}
          {...props}
        />
        {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
        {helperText && !error && <p className="mt-1 text-xs text-[#78837B]">{helperText}</p>}
      </div>
    );
  }
);

Textarea.displayName = 'Textarea';
