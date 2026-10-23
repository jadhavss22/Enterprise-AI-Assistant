"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

type Role = "employee" | "manager" | "admin";
type User = { user_id: string; email: string; display_name: string; role: Role; status: "active" | "disabled"; department_id: string };
type AuthContextValue = { user: User | null; loading: boolean; accessToken: string | null; login: (email: string, password: string) => Promise<void>; logout: () => Promise<void>; logoutAll: () => Promise<void> };

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

function csrfToken(): string | undefined {
  return document.cookie.split("; ").find((part) => part.startsWith("enterprise_csrf="))?.split("=")[1];
}

async function request(path: string, init: RequestInit = {}, token?: string | null): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.method && init.method !== "GET") {
    const csrf = csrfToken();
    if (csrf) headers.set("X-CSRF-Token", csrf);
  }
  return fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const restore = useCallback(async () => {
    try {
      const response = await request("/auth/refresh", { method: "POST" });
      if (response.ok) { const data = (await response.json()) as { access_token: string; user: User }; setAccessToken(data.access_token); setUser(data.user); }
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void restore(); }, [restore]);
  const value = useMemo<AuthContextValue>(() => ({
    user, accessToken, loading,
    async login(email, password) {
      const response = await request("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      if (!response.ok) throw new Error("Invalid email or password");
      const data = (await response.json()) as { access_token: string; user: User }; setAccessToken(data.access_token); setUser(data.user);
    },
    async logout() { await request("/auth/logout", { method: "POST" }, accessToken); setAccessToken(null); setUser(null); },
    async logoutAll() { await request("/auth/logout-all", { method: "POST" }, accessToken); setAccessToken(null); setUser(null); },
  }), [accessToken, loading, user]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
