// O storage simulado dos testes de upload. Com o e2e em HTTPS (#585), o
// navegador recusa enviar para um storage http (lesson-asset-panel.tsx, como
// em produção), então o storage dos testes também é https. O Playwright
// responde pelas rotas, sem servidor TLS nesse endereço.
export const E2E_STORAGE_ORIGIN = "https://127.0.0.1:8333";
