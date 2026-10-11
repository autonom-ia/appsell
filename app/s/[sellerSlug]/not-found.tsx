import Link from "next/link";

export default function StorefrontNotFound() {
  return <main id="conteudo-vitrine" className="storefront-state" tabIndex={-1}>
    <p className="eyebrow">Não encontrado</p>
    <h1>Esta vitrine não está disponível.</h1>
    <p>O endereço pode estar incorreto ou o produtor pode não ter produtos publicados neste momento.</p>
    <Link className="primary-button" href="/">Voltar para o início</Link>
  </main>;
}
