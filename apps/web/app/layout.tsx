import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "RepoEvo Agent",
  description: "Safe, evaluable software maintenance Agent",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body style={{ background: "#f8fafc", color: "#0f172a", fontFamily: "system-ui, sans-serif", margin: 0 }}>
        <div style={{ margin: "0 auto", maxWidth: 1080, padding: "40px 24px" }}>{children}</div>
      </body>
    </html>
  );
}
