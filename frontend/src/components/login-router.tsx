"use client";

import { Suspense } from "react";
import { LoginFormContent } from "./login-form";

export function LoginRouter() {
  return <Suspense fallback={<main className="gate"><p>Loading sign in…</p></main>}><LoginFormContent /></Suspense>;
}
