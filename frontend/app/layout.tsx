import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import { AuthProvider } from "../src/lib/auth-context";

export const metadata: Metadata = {
  title: "Enterprise AI Assistant",
  description: "Placeholder frontend for the Enterprise AI Assistant",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body><AuthProvider>{children}</AuthProvider></body>
    </html>
  );
}

