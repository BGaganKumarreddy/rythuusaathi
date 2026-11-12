import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider, useAuth } from '../context/AuthContext';
import { ToastProvider } from '../context/ToastContext';
import { LoginPage } from '../pages/auth/LoginPage';
import { RegisterPage } from '../pages/auth/RegisterPage';
import { authService } from '../services/authService';

vi.mock('../services/authService', () => ({
  authService: {
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    getMe: vi.fn(),
  },
}));

const renderWithProviders = (ui) => {
  return render(
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>{ui}</ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
};

describe('Authentication Flow Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('1. Renders Login page with fields and validation', async () => {
    renderWithProviders(<LoginPage />);

    expect(screen.getByText(/Welcome back/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Password/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Tenant ID/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign In/i })).toBeInTheDocument();
  });

  it('2. Submits valid login and saves token', async () => {
    authService.login.mockResolvedValueOnce({
      access_token: 'fake-jwt-token-12345',
      token_type: 'bearer',
      user_id: 'u-100',
      tenant_id: 'default',
      roles: ['member'],
      expires_at: '2026-11-13T10:00:00Z',
    });

    renderWithProviders(<LoginPage />);

    fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: 'farmer_ram' } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: 'SecretPassword123!' } });
    fireEvent.click(screen.getByRole('button', { name: /Sign In/i }));

    await waitFor(() => {
      expect(authService.login).toHaveBeenCalledWith({
        username: 'farmer_ram',
        password: 'SecretPassword123!',
        tenant_id: 'default',
      });
      expect(localStorage.getItem('rythusaathi_auth_token')).toBe('fake-jwt-token-12345');
    });
  });

  it('3. Handles API authentication failure gracefully', async () => {
    authService.login.mockRejectedValueOnce({
      status: 401,
      code: 'authentication_failed',
      message: 'Invalid username, password, or tenant ID',
    });

    renderWithProviders(<LoginPage />);

    fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: 'bad_user' } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: 'wrong_pass123' } });
    fireEvent.click(screen.getByRole('button', { name: /Sign In/i }));

    await waitFor(() => {
      const errorElements = screen.getAllByText(/Invalid username, password, or tenant ID/i);
      expect(errorElements.length).toBeGreaterThan(0);
    });
  });

  it('4. Registration validates password length', async () => {
    renderWithProviders(<RegisterPage />);

    fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: 'new_farmer' } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: 'short' } });
    fireEvent.click(screen.getByRole('button', { name: /Create Account/i }));

    await waitFor(() => {
      expect(screen.getByText(/Password must contain at least 12 characters/i)).toBeInTheDocument();
    });
    expect(authService.register).not.toHaveBeenCalled();
  });

  it('5. Registration calls API and proceeds with auto-login', async () => {
    authService.register.mockResolvedValueOnce({
      user_id: 'u-200',
      username: 'valid_user',
      tenant_id: 'coop-1',
      role: 'member',
    });
    authService.login.mockResolvedValueOnce({
      access_token: 'new-token-999',
      user_id: 'u-200',
      tenant_id: 'coop-1',
      roles: ['member'],
    });

    renderWithProviders(<RegisterPage />);

    fireEvent.change(screen.getByLabelText(/Username/i), { target: { value: 'valid_user' } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: 'StrongPassword1234!' } });
    fireEvent.change(screen.getByLabelText(/Tenant ID/i), { target: { value: 'coop-1' } });
    fireEvent.click(screen.getByRole('button', { name: /Create Account/i }));

    await waitFor(() => {
      expect(authService.register).toHaveBeenCalledWith({
        username: 'valid_user',
        password: 'StrongPassword1234!',
        tenant_id: 'coop-1',
        tenant_name: undefined,
        role: 'member',
      });
    });
  });
});
