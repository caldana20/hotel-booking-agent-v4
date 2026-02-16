import "./globals.css";
import React from "react";

export const metadata = {
  title: "Hotel Shopping Agent",
  description: "Webchat UI for the Hotel Shopping AI Agent MVP"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

