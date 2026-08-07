import "@fontsource-variable/ibm-plex-sans";
import "@fontsource-variable/jetbrains-mono";
import "./globals.css";

export const metadata = {
  title: "Alpha Sage",
  description: "自我学习进化的本地投资 Agent",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

