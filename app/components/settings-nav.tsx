import Link from "next/link";

// Configurações da loja tem duas áreas. A navegação é fixa de propósito: o
// vendedor precisa achar Recebimentos no mesmo lugar sempre.
export function SettingsNav({ active }: { active: "store" | "payouts" }) {
  const items = [
    { id: "store", href: "/settings/store", label: "Loja" },
    { id: "payouts", href: "/settings/payouts", label: "Recebimentos" }
  ] as const;
  return (
    <nav className="settings-nav" aria-label="Áreas das configurações">
      {items.map((item) => (
        <Link
          key={item.id}
          href={item.href}
          className={item.id === active ? "active" : undefined}
          aria-current={item.id === active ? "page" : undefined}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
