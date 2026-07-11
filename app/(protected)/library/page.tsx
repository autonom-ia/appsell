import { LibraryWorkspace } from "../../components/library-workspace";

export default function LibraryPage() {
  return (
    <>
      <header className="page-header"><div><p className="eyebrow">Área do aluno</p><h1>Minha biblioteca</h1><p>Somente produtos publicados com acesso ativo para esta conta.</p></div></header>
      <LibraryWorkspace/>
    </>
  );
}
