import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "Listing Studio · Amazon Listing 工作台",
  description: "Amazon 多站点 Listing 诊断、文案审核与版本复评。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
