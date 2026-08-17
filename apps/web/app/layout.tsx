import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SF Gyms | Find your fit",
  description: "Compare San Francisco gyms by price, neighborhood, amenities, and vibe.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
