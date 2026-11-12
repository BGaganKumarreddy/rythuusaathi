import React, { useEffect } from 'react';
import { X } from 'lucide-react';

export const Modal = ({
  isOpen,
  onClose,
  title,
  subtitle,
  children,
  maxWidth = 'max-w-xl',
  footer,
}) => {
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && isOpen) onClose();
    };
    if (isOpen) {
      document.body.style.overflow = 'hidden';
      window.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      document.body.style.overflow = 'unset';
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 overflow-y-auto">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs transition-opacity animate-in fade-in"
        onClick={onClose}
      />

      {/* Modal Dialog */}
      <div
        className={`relative bg-white rounded-2xl border border-[#E6EAE4] shadow-2xl w-full ${maxWidth} z-10 overflow-hidden animate-in fade-in zoom-in-95 duration-150`}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-start justify-between px-6 py-5 border-b border-[#E6EAE4]">
          <div>
            <h3 className="text-lg font-semibold text-[#202B24]">{title}</h3>
            {subtitle && <p className="text-xs text-[#78837B] mt-0.5">{subtitle}</p>}
          </div>
          <button
            onClick={onClose}
            className="text-[#78837B] hover:text-[#202B24] p-1 rounded-lg hover:bg-[#F7F8F4] transition-colors"
            aria-label="Close dialog"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 max-h-[75vh] overflow-y-auto">{children}</div>

        {footer && (
          <div className="flex items-center justify-end gap-3 px-6 py-4 bg-[#F7F8F4] border-t border-[#E6EAE4]">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};
