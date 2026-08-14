import "./globals.css";

export const metadata = {
  title: "Legal_AIdvisor",
  description: "Asistent juridic ancorat in legislatia romaneasca, cu citari verificate.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ro">
      <body>{children}</body>
    </html>
  );
}
