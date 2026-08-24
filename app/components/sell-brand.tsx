interface SellBrandProps {
  onLight?: boolean;
}

export function SellBrand({ onLight = false }: SellBrandProps) {
  return (
    <span className={onLight ? "sell-brand sell-brand-on-light" : "sell-brand"}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        className="sell-brand-mark"
        src="/autonomia-icon.png"
        width={44}
        height={44}
        alt=""
        aria-hidden="true"
      />
      <span className="sell-brand-copy">
        <span className="sell-brand-name">
          sell<span className="sell-brand-dot">.</span>
        </span>
        <span className="sell-brand-tag">BY AUTONOM.IA</span>
      </span>
    </span>
  );
}
