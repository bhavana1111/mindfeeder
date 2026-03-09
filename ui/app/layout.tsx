import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Signal Explorer",
  description: "View and analyze events processed by the Mindfeeder system.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen" style={{ background: "#080b0f" }}>
        {/* Top bar */}
        <header
          style={{
            borderBottom: "1px solid #1e2a36",
            background: "#0d1117",
            padding: "0 24px",
            height: 48,
            display: "flex",
            alignItems: "center",
            gap: 10,
            position: "sticky",
            top: 0,
            zIndex: 50,
          }}
        >
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "hsl(38 96% 47%)",
            }}
          />
          <span
            style={{
              fontFamily: "var(--font-mono), monospace",
              fontSize: 13,
              fontWeight: 600,
              letterSpacing: "0.12em",
              color: "hsl(38 96% 47%)",
            }}
          >
            Signal Explorer
          </span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
