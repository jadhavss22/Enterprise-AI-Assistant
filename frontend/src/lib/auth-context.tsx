"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type Role = "employee" | "manager" | "admin";
export type User = { user_id: string; email: string; display_name: string; role: Role; status: "active" | "disabled"; department_id: string };
type AuthPayload = { access_token: string; token_type: "bearer"; expires_in: number; user: User; csrf_token: string; session_expires_at: string };
export type AuthStatus = "loading" | "authenticated" | "anonymous" | "unavailable";
export type ApiError = Error & { status: number; code: string; requestId?: string; retryAfter?: string };
type ContextValue = { user: User | null; status: AuthStatus; message: string | null; login: (email: string, password: string) => Promise<void>; logout: () => Promise<void>; logoutAll: () => Promise<void>; refresh: () => Promise<void>; api: <T>(path: string, init?: RequestInit) => Promise<T> };

const Context = createContext<ContextValue | undefined>(undefined);
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
const CHANNEL_NAME = "enterprise-ai-assistant-session";
const LOCK_NAME = "enterprise-ai-assistant-refresh-cookie";

function apiError(response: Response, payload: unknown): ApiError {
  const body = payload && typeof payload === "object" ? payload as { error?: { code?: string; message?: string; request_id?: string } } : {};
  const error = new Error(body.error?.message ?? `Request failed (${response.status})`) as ApiError;
  error.status = response.status;
  error.code = body.error?.code ?? `HTTP_${response.status}`;
  error.requestId = body.error?.request_id ?? response.headers.get("X-Request-ID") ?? undefined;
  error.retryAfter = response.headers.get("Retry-After") ?? undefined;
  return error;
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let payload: unknown;
    try { payload = await response.json(); } catch { payload = undefined; }
    throw apiError(response, payload);
  }
  if (response.status === 204) return undefined as T;
  return await response.json() as T;
}

function lock<T>(operation: () => Promise<T>): Promise<T> {
  if (typeof navigator === "undefined" || !navigator.locks) {
    return Promise.reject(new Error("This browser does not support secure cross-tab session coordination. Use a current version of Chrome or Edge."));
  }
  return (async () => {
    for (let attempt = 0; attempt < 50; attempt += 1) {
      let acquired = false;
      const result = await navigator.locks.request(LOCK_NAME, { mode: "exclusive", ifAvailable: true }, async (resource) => {
        if (!resource) return undefined as T;
        acquired = true;
        return await operation();
      }).then((value) => value);
      if (acquired) return result;
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    throw new Error("Another tab is still updating the sign-in session. Wait a moment and retry.");
  })();
}

async function fetchJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const url = new URL(path.replace(/^\//, ""), `${API_BASE.replace(/\/$/, "")}/`);
  if (url.origin !== new URL(API_BASE).origin || !path.startsWith("/") || path.startsWith("//") || path.includes("..")) {
    throw new Error("API requests must use a safe relative path.");
  }
  const headers = new Headers(init.headers);
  if (typeof init.body === "string" && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(url, { ...init, headers, credentials: "include", cache: "no-store", signal: init.signal ?? AbortSignal.timeout(20000) });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new Error("The API is unreachable. Check that the local services are running, then retry.");
  }
  return readJson<T>(response);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [message, setMessage] = useState<string | null>(null);
  const tokenRef = useRef<string | null>(null);
  const csrfRef = useRef<string | null>(null);
  const userRef = useRef<User | null>(null);
  const expiresAtRef = useRef(0);
  const refreshFlight = useRef<Promise<void> | null>(null);
  const channelRef = useRef<BroadcastChannel | null>(null);

  const storeSession = useCallback((data: AuthPayload) => {
    tokenRef.current = data.access_token;
    csrfRef.current = data.csrf_token;
    userRef.current = data.user;
    expiresAtRef.current = Date.now() + Math.max(0, data.expires_in) * 1000;
    setUser(data.user);
    setStatus("authenticated");
    setMessage(null);
  }, []);

  const clearSession = useCallback((nextMessage: string | null = null) => {
    tokenRef.current = null;
    csrfRef.current = null;
    userRef.current = null;
    expiresAtRef.current = 0;
    setUser(null);
    setStatus("anonymous");
    setMessage(nextMessage);
  }, []);

  const issueTokensUnderLock = useCallback(async (): Promise<AuthPayload> => {
    const csrf = await fetchJson<{ csrf_token: string }>("/auth/csrf", { method: "GET" });
    const data = await fetchJson<AuthPayload>("/auth/refresh", {
      method: "POST",
      headers: { "X-CSRF-Token": csrf.csrf_token },
    });
    csrfRef.current = data.csrf_token;
    return data;
  }, []);

  const refresh = useCallback(async () => {
    if (refreshFlight.current) return refreshFlight.current;
    const flight = lock(async () => {
      try {
        const data = await issueTokensUnderLock();
        storeSession(data);
      } catch (error) {
        const statusCode = (error as ApiError).status;
        if (statusCode === 401) clearSession();
        else {
          setStatus("unavailable");
          setMessage(error instanceof Error ? error.message : "Session could not be restored.");
          throw error;
        }
      }
    });
    refreshFlight.current = flight;
    try { await flight; }
    catch (error) {
      setStatus("unavailable");
      setMessage(error instanceof Error ? error.message : "Session coordination is unavailable in this browser.");
      throw error;
    } finally { refreshFlight.current = null; }
  }, [clearSession, issueTokensUnderLock, storeSession]);

  const login = useCallback(async (email: string, password: string) => {
    await lock(async () => {
      const data = await fetchJson<AuthPayload>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), password }),
      });
      storeSession(data);
      channelRef.current?.postMessage({ type: "session-changed" });
    });
  }, [storeSession]);

  const api = useCallback(async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    if (!tokenRef.current || !user) throw new Error("Sign in to continue.");
    const identity = user.user_id;
    const send = (token: string) => {
      const headers = new Headers(init.headers);
      headers.set("Authorization", `Bearer ${token}`);
      return fetchJson<T>(path, { ...init, headers });
    };
    try { return await send(tokenRef.current); }
    catch (error) {
      if ((error as ApiError).status !== 401) throw error;
      await refresh();
      if (!tokenRef.current || userRef.current?.user_id !== identity) throw new Error("Your session changed. Please retry this action.");
      return send(tokenRef.current);
    }
  }, [refresh, user]);

  const logout = useCallback(async () => {
    await lock(async () => {
      const csrf = csrfRef.current ?? (await fetchJson<{ csrf_token: string }>("/auth/csrf")).csrf_token;
      await fetchJson<void>("/auth/logout", { method: "POST", headers: { "X-CSRF-Token": csrf } });
      clearSession();
      channelRef.current?.postMessage({ type: "session-changed" });
    });
  }, [clearSession]);

  const logoutAll = useCallback(async () => {
    if (!user) throw new Error("Sign in to continue.");
    const originalUserId = user.user_id;
    await lock(async () => {
      const data = await issueTokensUnderLock();
      if (data.user.user_id !== originalUserId) throw new Error("The active account changed in another tab. Please retry.");
      await fetchJson<void>("/auth/logout-all", {
        method: "POST",
        headers: { Authorization: `Bearer ${data.access_token}`, "X-CSRF-Token": data.csrf_token },
      });
      clearSession();
      channelRef.current?.postMessage({ type: "session-changed" });
    });
  }, [clearSession, issueTokensUnderLock, user]);

  const restore = useCallback(async () => {
    try { await refresh(); }
    catch { /* unavailable state and a retry message are set by refresh */ }
  }, [refresh]);

  const revalidate = useCallback(async () => {
    if (status !== "authenticated") return;
    if (expiresAtRef.current - Date.now() < 45000) {
      try { await refresh(); } catch { return; }
    }
    try {
      const current = await api<User>("/users/me");
      if (current.status !== "active") clearSession("This account is disabled.");
      else { userRef.current = current; setUser(current); }
    } catch (error) {
      if ((error as ApiError).status === 401) clearSession("Your session has ended. Sign in again.");
      else if ((error as ApiError).status === 503) setMessage("The service is temporarily unavailable; your session will be checked again.");
    }
  }, [api, clearSession, refresh, status]);

  useEffect(() => { void Promise.resolve().then(restore); }, [restore]);
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel(CHANNEL_NAME);
    channelRef.current = channel;
    channel.onmessage = (event: MessageEvent<{ type?: string }>) => {
      if (event.data?.type === "session-changed") void restore();
    };
    return () => { channel.close(); channelRef.current = null; };
  }, [restore]);
  useEffect(() => {
    if (status !== "authenticated") return;
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void revalidate(); }, 60000);
    const onFocus = () => { if (document.visibilityState === "visible") void revalidate(); };
    window.addEventListener("focus", onFocus);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", onFocus); };
  }, [revalidate, status]);

  const value = useMemo(() => ({ user, status, message, login, logout, logoutAll, refresh, api }), [api, login, logout, logoutAll, message, refresh, status, user]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useAuth() {
  const value = useContext(Context);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
