import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const webDockerfile = readFileSync("deploy/easypanel-native/Dockerfile.web", "utf8");
const apiDockerfile = readFileSync("deploy/easypanel-native/Dockerfile.api", "utf8");
const bootstrapDockerfile = readFileSync(
  "deploy/easypanel-native/Dockerfile.bootstrap",
  "utf8"
);
const bootstrap = readFileSync(
  "deploy/easypanel-native/bootstrap-native-demo.sh",
  "utf8"
);
const readme = readFileSync("deploy/easypanel-native/README.md", "utf8");
const workflow = readFileSync(".github/workflows/easypanel-native-images.yml", "utf8");
const appCi = readFileSync(".github/workflows/app-ci.yml", "utf8");

describe("EasyPanel native runtime contract", () => {
  it("uses EasyPanel GIT_SHA and standalone runtimes without Playwright", () => {
    for (const dockerfile of [webDockerfile, apiDockerfile, bootstrapDockerfile]) {
      expect(dockerfile).toContain("ARG GIT_SHA");
      expect(dockerfile).toContain('test -n "${GIT_SHA}"');
      expect(dockerfile).not.toContain("SOURCE_SHA");
      expect(dockerfile).not.toContain("mcr.microsoft.com/playwright");
    }
    expect(readFileSync("next.config.ts", "utf8")).toContain('output: "standalone"');
    expect(webDockerfile).toContain("pnpm install --frozen-lockfile --prod=false");
    expect(webDockerfile).toContain("NODE_ENV=production corepack pnpm build");
    expect(webDockerfile).toContain("/app/.next/standalone");
    expect(webDockerfile).toContain('CMD ["node", "server.js"]');
    expect(webDockerfile).toContain("/api/health/ready");
    expect(webDockerfile).not.toContain("/api/health/live').then");
  });

  it("builds affected images on the integrated SHA and keeps full manual rehearsal", () => {
    expect(appCi).toContain("merge_group:");
    expect(appCi).toContain("GIT_SHA=${{ github.sha }}");
    expect(workflow).toContain("workflow_dispatch:");
    expect(workflow).not.toContain("pull_request:");
    for (const file of ["Dockerfile.web", "Dockerfile.api", "Dockerfile.bootstrap"]) {
      expect(workflow).toContain(`deploy/easypanel-native/${file}`);
      expect(appCi).toContain(`deploy/easypanel-native/${file}`);
    }
    expect(workflow).toContain("GIT_SHA=${{ env.SOURCE_SHA }}");
    expect(workflow).toContain("push: false");
    expect(appCi).toContain("push: false");
  });

  it("keeps protected PDF originals out of both public runtime images", () => {
    for (const protectedPdf of [
      "01-o-que-e-codex.pdf",
      "02-contexto-repo-objetivo.pdf",
      "03-implementar-com-seguranca.pdf",
      "04-revisar-testes-pr.pdf"
    ]) {
      expect(webDockerfile).toContain(`test ! -e /tmp/web-assets/output/pdf/codex/${protectedPdf}`);
      expect(apiDockerfile).not.toContain(protectedPdf);
    }
    expect(webDockerfile).toContain("05-deploy-e-rollback.pdf");
    expect(webDockerfile).toContain("codex-em-3-passos.mp4");
    expect(apiDockerfile).not.toMatch(/COPY output\/pdf\/codex\/[^\n]+\.pdf/);
    expect(apiDockerfile).toContain("-type f ! -name '*.png'");
  });

  it("limits bootstrap mutations to the exact native database and roles", () => {
    for (const exact of [
      "sell-autonomia_postgres-native-90",
      "appsell_native_90",
      "appsell_native_90_migrator",
      "appsell_native_90_runtime",
      "appsell-native-90-v1"
    ]) {
      expect(bootstrap).toContain(exact);
    }
    expect(bootstrap).toContain("before the first mutation");
    expect(bootstrap).not.toContain("appsell_preview_77");
    expect(bootstrap).not.toMatch(/\b(?:flow|dados|n8n)[_-]/);
    expect(bootstrap).not.toMatch(/\bdocker\b/);
    expect(bootstrap).not.toMatch(/\b(?:drop|truncate)\b/i);
    expect(bootstrap.indexOf("python -m appsell_api.reset_demo_tenant"))
      .toBeLessThan(bootstrap.lastIndexOf("database_marker"));
    expect(bootstrap.indexOf("app_private.schema_ready()"))
      .toBeLessThan(bootstrap.lastIndexOf("database_marker"));
    expect(bootstrapDockerfile).toContain('CMD ["sleep", "infinity"]');
    expect(readme).toContain("Console nativo do EasyPanel");
    expect(readme).toContain("executar exatamente uma vez");
    expect(bootstrap).toContain("native bootstrap is already running in this container");
    expect(bootstrapDockerfile).not.toContain("EXPOSE");
  });

  it("documents public staging honestly and preserves the old runtime for rollback", () => {
    expect(readme).toMatch(/homologação pública do\s+demo em modo/);
    expect(readme).toContain("não pode ser promovido para");
    expect(readme).toContain("sell-issue90.85-31-60-100.sslip.io");
    expect(readme).toContain("runtime-preview` e o");
    expect(readme).toContain("PDFs-aula 1–4 não entram");
    expect(readme).toContain("Não existe marker local-demo de produção");
    expect(readme).toContain("assina `state + persona`");
  });
});
