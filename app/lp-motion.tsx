"use client";

import { useEffect } from "react";

// A coreografia da pagina publica. Fica separada do markup porque o servidor
// entrega a pagina inteira legivel sem ela: se este componente nao rodar, o CSS
// em `html:not(.js-anim)` empilha os tres atos e mostra tudo. Nada aqui e
// requisito para ler a pagina, so para a leitura ter ritmo.
export function LpMotion() {
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let vivo = true;
    let limpar: (() => void) | undefined;

    void (async () => {
      const [{ gsap }, { ScrollTrigger }, { default: Lenis }] = await Promise.all([
        import("gsap"),
        import("gsap/ScrollTrigger"),
        import("lenis")
      ]);
      if (!vivo) return;

      gsap.registerPlugin(ScrollTrigger);
      ScrollTrigger.config({ ignoreMobileResize: true });
      document.documentElement.classList.add("js-anim");

      const lenis = new Lenis({ duration: 1.1, smoothWheel: true });
      lenis.on("scroll", ScrollTrigger.update);
      const tick = (t: number) => lenis.raf(t * 1000);
      gsap.ticker.add(tick);
      gsap.ticker.lagSmoothing(0);
      if ("scrollRestoration" in history) history.scrollRestoration = "manual";

      const atos = gsap.utils.toArray<HTMLElement>(".ato");
      const barras = gsap.utils.toArray<HTMLElement>(".progresso i");
      const topo = document.querySelector(".top");
      const estreito = window.matchMedia("(max-width: 999px)");
      const emPe = window.matchMedia("(max-width: 859px)");

      const ancoras = (event: Event) => {
        const alvo = (event.currentTarget as HTMLAnchorElement).getAttribute("href");
        const destino = alvo ? document.querySelector(alvo) : null;
        if (!destino) return;
        event.preventDefault();
        const altura = parseFloat(
          getComputedStyle(document.documentElement).getPropertyValue("--topo-h")
        ) || 81;
        lenis.scrollTo(destino as HTMLElement, { offset: -Math.round(altura) - 16 });
      };
      const links = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]'));
      links.forEach((a) => a.addEventListener("click", ancoras));

      gsap.set(atos, { autoAlpha: 0 });
      gsap.set(atos[0], { autoAlpha: 1 });
      gsap.set(".ato .linha > span", { yPercent: 110 });
      gsap.set(atos[0].querySelectorAll(".linha > span"), { yPercent: 0 });
      gsap.set(["#t-painel", "#t-recebe"], { clipPath: "inset(0 0 0 100%)" });
      gsap.set("#peca-painel", { autoAlpha: 0, z: 40 });

      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: ".abertura .pin",
          start: "top top",
          end: estreito.matches ? "+=100%" : "+=140%",
          pin: true,
          scrub: 0.7,
          anticipatePin: 1,
          onUpdate: (self) => {
            const i = self.progress < 0.3 ? 0 : self.progress < 0.64 ? 1 : 2;
            barras.forEach((b, k) => b.classList.toggle("on", k === i));
            // O estado de acessibilidade sai do mesmo indice que move o
            // indicador: o ato na tela e o unico anunciado.
            atos.forEach((a, k) => a.setAttribute("aria-hidden", String(k !== i)));
          }
        }
      });

      // A inclinacao anda pouco: acima de 15 graus o texto da interface entra
      // em keystone e para de ser legivel.
      gsap.set("#corpo-hero", { rotateY: -13, rotateX: 6, rotateZ: -1.5 });
      tl.to("#corpo-hero", { rotateY: -4, rotateX: 2, rotateZ: 0, ease: "none", duration: 0.66 }, 0)
        .to("#foto", { yPercent: -8, scale: 1.1, ease: "none", duration: 1 }, 0)
        .to("#foto", { opacity: 0.22, ease: "none", duration: 0.28 }, 0.14);

      const troca = (saiu: HTMLElement, entrou: HTMLElement, momento: number) => {
        tl.to(saiu.querySelectorAll(".linha > span"), { yPercent: -110, duration: 0.02, stagger: 0.006, ease: "power2.in" }, momento)
          .to([saiu.querySelector(".eb"), saiu.querySelector(".sub"), saiu.querySelector(".actions"), saiu.querySelector(".preco-linha"), saiu.querySelector(".nota")].filter(Boolean),
            { autoAlpha: 0, y: -14, duration: 0.02 }, momento)
          .set(saiu, { autoAlpha: 0 }, momento + 0.027)
          .set(entrou, { autoAlpha: 1 }, momento + 0.028)
          .fromTo(entrou.querySelectorAll(".linha > span"), { yPercent: 110 }, { yPercent: 0, duration: 0.06, stagger: 0.016, ease: "power3.out" }, momento + 0.028)
          .fromTo(entrou.querySelector(".eb"), { autoAlpha: 0, y: 10 }, { autoAlpha: 1, y: 0, duration: 0.04 }, momento + 0.038)
          .fromTo(entrou.querySelector(".sub"), { autoAlpha: 0, y: 18 }, { autoAlpha: 1, y: 0, duration: 0.05 }, momento + 0.05);
      };
      troca(atos[0], atos[1], 0.3);
      troca(atos[1], atos[2], 0.64);

      // A tela troca por cortina. Fade cruzado deixaria duas interfaces
      // legiveis no mesmo aparelho.
      tl.to("#t-painel", { clipPath: "inset(0 0 0 0%)", ease: "power2.inOut", duration: 0.022 }, 0.315)
        .fromTo("#peca-painel", { autoAlpha: 0, z: 40 }, { autoAlpha: 1, z: 90, duration: 0.05, ease: "power3.out" }, 0.39)
        .to("#peca-painel", { autoAlpha: 0, z: 40, duration: 0.032, ease: "power2.in" }, 0.615)
        .to("#t-recebe", { clipPath: "inset(0 0 0 0%)", ease: "power2.inOut", duration: 0.022 }, 0.655)
        // No ato do dinheiro o aparelho para de girar.
        .to("#corpo-hero", { rotateY: 0, rotateX: 0, ease: "power2.out", duration: 0.14, overwrite: "auto" }, 0.66);

      tl.to(".palco", { autoAlpha: 0, y: "-5vh", ease: "power1.in", duration: 0.08 }, 0.92)
        .to(".progresso", { autoAlpha: 0, duration: 0.05 }, 0.94);

      // Um laranja por tela: o botao do topo so assume a cor depois do hero, e
      // recua onde a secao de preco ja oferece a mesma acao.
      ScrollTrigger.create({
        trigger: ".tese", start: "top 94%", end: "max",
        onToggle: (self) => topo?.classList.toggle("solido", self.isActive)
      });
      ScrollTrigger.create({
        trigger: ".preco .actions", start: "top bottom", end: "bottom top",
        onToggle: (self) => topo?.classList.toggle("cede", self.isActive)
      });

      gsap.to("#via-luz", emPe.matches
        ? { height: "100%", ease: "none", scrollTrigger: { trigger: ".trilho", start: "top 84%", end: "bottom 70%", scrub: true } }
        : { width: "100%", ease: "none", scrollTrigger: { trigger: ".trilho", start: "top 78%", end: "bottom 62%", scrub: true } });
      gsap.from(".no", {
        y: 26, autoAlpha: 0, duration: 0.55, stagger: 0.14, ease: "power2.out",
        scrollTrigger: { trigger: ".trilho", start: "top 76%", toggleActions: "play none none none" }
      });

      gsap.fromTo("#corpo-loja", { rotateY: 16, rotateX: 5, y: 40, autoAlpha: 0 },
        { rotateY: 11, rotateX: 3, y: 0, autoAlpha: 1, duration: 1, ease: "power3.out",
          scrollTrigger: { trigger: ".palco-loja", start: "top 84%", toggleActions: "play none none none" } });
      gsap.fromTo("#peca-aula", { autoAlpha: 0, z: 40 },
        { autoAlpha: 1, z: 90, duration: 0.4, ease: "power3.out",
          scrollTrigger: { trigger: ".palco-loja", start: "top 66%", toggleActions: "play none none none" } });

      gsap.utils.toArray<HTMLElement>(".tese .in, .linhas > div, .preco .numero-grid, .perguntas .in, .contato .in")
        .forEach((el) => {
          gsap.from(el, {
            y: 24, autoAlpha: 0, duration: 0.7, ease: "power2.out",
            scrollTrigger: { trigger: el, start: "top 88%", toggleActions: "play none none none" }
          });
        });

      // O video de fundo e bonus, nao requisito: entra depois que a pagina ja
      // esta pronta, so onde ele e visto, e some sem deixar rastro se algo
      // falhar. Em telas pequenas a foto vira textura atras do scrim, entao
      // baixar 300 KB ali seria dado gasto por nada.
      const conexao = (navigator as Navigator & {
        connection?: { saveData?: boolean; effectiveType?: string };
      }).connection;
      const querVideo =
        window.matchMedia("(min-width: 1000px)").matches &&
        conexao?.saveData !== true &&
        (conexao?.effectiveType === undefined || conexao.effectiveType === "4g");

      if (querVideo) {
        const entrar = () => {
          const capa = document.getElementById("foto");
          if (!capa || document.getElementById("video-foto")) return;
          const video = document.createElement("video");
          video.id = "video-foto";
          video.muted = true;
          video.loop = true;
          video.playsInline = true;
          video.preload = "none";
          video.setAttribute("aria-hidden", "true");
          for (const [src, type] of [["/lp/hero.webm", "video/webm"], ["/lp/hero.mp4", "video/mp4"]]) {
            const fonte = document.createElement("source");
            fonte.src = src;
            fonte.type = type;
            video.appendChild(fonte);
          }
          video.addEventListener("canplaythrough", () => {
            void video.play().then(() => video.classList.add("pronto")).catch(() => video.remove());
          }, { once: true });
          video.addEventListener("error", () => video.remove(), { once: true });
          capa.appendChild(video);
          video.load();
        };
        if ("requestIdleCallback" in window) {
          (window as Window & { requestIdleCallback: (cb: () => void, o?: { timeout: number }) => void })
            .requestIdleCallback(entrar, { timeout: 3000 });
        } else {
          setTimeout(entrar, 1200);
        }
      }

      const refrescar = () => ScrollTrigger.refresh();
      void document.fonts?.ready.then(refrescar);
      window.addEventListener("orientationchange", refrescar);

      limpar = () => {
        links.forEach((a) => a.removeEventListener("click", ancoras));
        window.removeEventListener("orientationchange", refrescar);
        document.getElementById("video-foto")?.remove();
        gsap.ticker.remove(tick);
        ScrollTrigger.getAll().forEach((s) => s.kill());
        lenis.destroy();
        document.documentElement.classList.remove("js-anim");
      };
    })();

    return () => {
      vivo = false;
      limpar?.();
    };
  }, []);

  return null;
}
