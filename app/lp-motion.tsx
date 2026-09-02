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
      document.documentElement.dataset.motion = "on";

      const lenis = new Lenis({ duration: 0.9, smoothWheel: true });
      lenis.on("scroll", ScrollTrigger.update);
      const tick = (t: number) => lenis.raf(t * 1000);
      gsap.ticker.add(tick);
      gsap.ticker.lagSmoothing(0);

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
      // A inclinacao anda pouco: acima de 15 graus o texto da interface entra
      // em keystone e para de ser legivel.
      gsap.set("#corpo-hero", { rotateY: -13, rotateX: 6, rotateZ: -1.5 });

      // A passagem entre atos toca no tempo, nao no scroll. Filmada a 28 fps,
      // a versao presa ao scrub cabia em 4 a 8 quadros: quem rola a 1500 px/s
      // atravessava a troca em 150 ms, e o olho lia corte. Aqui o scroll so
      // decide qual ato esta na tela; a saida e a entrada duram o mesmo com
      // roda lenta, flick de polegar ou trackpad. A entrada comeca quando a
      // saida ja esta 96% fora da mascara: as mascaras dos atos nao coincidem
      // em altura, entao qualquer sobra do titulo antigo aparece por cima do
      // novo.
      const T_SAIDA = 0.34;
      const T_ENTRADA = 0.62;
      const CRUZE = 0.04;
      // O ultimo ato leva a maior fatia do curso: a entrada dura 0,9 s e num
      // flick o unpin cortava o preco no meio. O primeiro ja foi lido antes
      // de qualquer rolagem. A folga evita que um scroll parado em cima do
      // limite fique trocando de ato.
      // No celular o pin e menor e o polegar anda mais rapido: o ultimo ato
      // precisa de metade do curso para a entrada terminar antes do unpin.
      const LIMITES = estreito.matches ? [0.24, 0.48] : [0.28, 0.56];
      const FOLGA = 0.03;
      const inicio = performance.now();

      const secundarios = (alvo: HTMLElement) =>
        [".eb", ".sub", ".actions", ".preco-linha", ".nota"]
          .map((s) => alvo.querySelector<HTMLElement>(s))
          .filter((el): el is HTMLElement => el !== null);
      const linhas = (alvo: HTMLElement) => alvo.querySelectorAll<HTMLElement>(".linha > span");

      let atual = 0;
      let passagem: gsap.core.Timeline | undefined;

      const trocar = (para: number, sentido: 1 | -1, imediato: boolean) => {
        const de = atual;
        atual = para;
        passagem?.kill();
        const saiu = atos[de];
        const entrou = atos[para];

        // Indicador, leitor de tela, texto e aparelho saem do mesmo evento.
        // Antes o indicador lia o scroll cru e o texto a timeline com scrub:
        // 110 px de diferenca no celular, 220 no desktop.
        atos.forEach((a, k) => {
          if (k !== de && k !== para) gsap.set(a, { autoAlpha: 0 });
          a.setAttribute("aria-hidden", String(k !== para));
        });
        barras.forEach((b, k) => b.classList.toggle("on", k === para));

        // Se o ato que entra ainda estava saindo (quem inverteu o scroll no
        // meio), ele continua de onde esta em vez de pular para fora.
        const escondido = Number(gsap.getProperty(entrou, "opacity")) < 0.02;
        if (escondido) {
          gsap.set(linhas(entrou), { yPercent: 110 * sentido });
          gsap.set(secundarios(entrou), { autoAlpha: 0, y: 14 * sentido });
        }

        const entra = T_SAIDA - CRUZE;
        const tl = gsap.timeline();
        tl.to(linhas(saiu), { yPercent: -110 * sentido, duration: T_SAIDA, stagger: 0.03, ease: "power2.inOut" }, 0)
          .to(secundarios(saiu), { autoAlpha: 0, y: -12 * sentido, duration: T_SAIDA * 0.8, ease: "power2.in" }, 0)
          .set(saiu, { autoAlpha: 0 }, T_SAIDA)
          .set(entrou, { autoAlpha: 1 }, entra)
          // Ordem de leitura: eyebrow, titulo, apoio. Uma .linha que quebra em
          // duas linhas visuais entrega a segunda ~200 ms depois da primeira;
          // o apoio espera por ela para nao chegar antes do fim do titulo.
          .to(entrou.querySelector(".eb"), { autoAlpha: 1, y: 0, duration: 0.4, ease: "power2.out" }, entra)
          .to(linhas(entrou), { yPercent: 0, duration: T_ENTRADA, stagger: 0.06, ease: "power3.out" }, entra + 0.05)
          .to(entrou.querySelector(".sub"), { autoAlpha: 1, y: 0, duration: 0.45, ease: "power2.out" }, entra + 0.36)
          .to(secundarios(entrou).filter((el) => !el.matches(".eb, .sub")),
            { autoAlpha: 1, y: 0, duration: 0.45, stagger: 0.05, ease: "power2.out" }, entra + 0.46);

        // A tela troca por cortina. Fade cruzado deixaria duas interfaces
        // legiveis no mesmo aparelho.
        tl.to("#t-painel", { clipPath: para >= 1 ? "inset(0 0 0 0%)" : "inset(0 0 0 100%)", duration: 0.5, ease: "power2.inOut" }, entra)
          .to("#t-recebe", { clipPath: para >= 2 ? "inset(0 0 0 0%)" : "inset(0 0 0 100%)", duration: 0.5, ease: "power2.inOut" }, entra)
          .to("#peca-painel",
            para === 1
              ? { autoAlpha: 1, z: 90, duration: 0.5, ease: "power3.out" }
              : { autoAlpha: 0, z: 40, duration: 0.3, ease: "power2.in" },
            para === 1 ? entra + 0.3 : 0)
          // No ato do dinheiro o aparelho para de girar. O giro continuo vive
          // no #corpo-hero, preso ao scroll; endireitar e no pai, para as duas
          // animacoes nao disputarem a mesma propriedade.
          .to(".palco .fone", { rotateY: para === 2 ? 4 : 0, rotateX: para === 2 ? -2 : 0, duration: 0.7, ease: "power2.out" }, entra);

        if (imediato) tl.progress(1);
        passagem = tl;
      };

      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: ".abertura .pin",
          start: "top top",
          // No celular o pin era 180% da tela com 500 px de nada entre trocas:
          // o polegar rolava e a pagina parecia presa.
          end: estreito.matches ? "+=150%" : "+=300%",
          pin: true,
          scrub: 0.45,
          anticipatePin: 1,
          onUpdate: (self) => {
            const p = self.progress;
            let alvo = atual;
            while (alvo < 2 && p > LIMITES[alvo] + FOLGA) alvo++;
            while (alvo > 0 && p < LIMITES[alvo - 1] - FOLGA) alvo--;
            if (alvo === atual) return;
            // Chegar no meio do pin (voltar de /termos) nao e uma passagem.
            trocar(alvo, alvo > atual ? 1 : -1, performance.now() - inicio < 600);
          }
        }
      });

      tl.to("#corpo-hero", { rotateY: -4, rotateX: 2, rotateZ: 0, ease: "none", duration: 0.66 }, 0)
        .to("#foto", { yPercent: -14, scale: 1.16, ease: "none", duration: 1 }, 0)
        .to("#foto", { opacity: 0.22, ease: "none", duration: 0.28 }, 0.14)
        // A deriva e pequena de proposito: a camera parece viva durante a
        // leitura sem competir com a passagem.
        .fromTo(".atos", { y: 12 }, { y: -12, ease: "none", duration: 1 }, 0)
        .to(".palco", { autoAlpha: 0, y: "-5vh", ease: "power1.in", duration: 0.08 }, 0.92)
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
        window.matchMedia("(min-width: 1000px) and (pointer: fine)").matches &&
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
          for (const [src, type] of [
            ["/lp/hero.webm", 'video/webm; codecs="av01.0.05M.08"'],
            ["/lp/hero.mp4", 'video/mp4; codecs="avc1.64001f"']
          ]) {
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

      // Com a restauracao do navegador desligada, voltar de /termos ou do
      // cadastro devolveria a pagina no topo. A posicao e guardada na saida e
      // reaplicada depois que o ScrollTrigger ja mediu: antes disso o pin
      // recalcularia por cima e o salto voltaria.
      const CHAVE = "lp:scroll";
      const guardar = () => {
        try { sessionStorage.setItem(CHAVE, String(Math.round(window.scrollY))); } catch { /* sessao indisponivel */ }
      };
      window.addEventListener("pagehide", guardar);
      try {
        const salvo = Number(sessionStorage.getItem(CHAVE));
        const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
        if (salvo > 0 && nav?.type === "back_forward") lenis.scrollTo(salvo, { immediate: true });
      } catch { /* sessao indisponivel */ }

      const refrescar = () => ScrollTrigger.refresh();
      const refrescarDepois = () =>
        requestAnimationFrame(() => requestAnimationFrame(refrescar));
      void document.fonts?.ready.then(refrescar);
      window.addEventListener("orientationchange", refrescarDepois);
      screen.orientation?.addEventListener("change", refrescarDepois);

      limpar = () => {
        links.forEach((a) => a.removeEventListener("click", ancoras));
        window.removeEventListener("pagehide", guardar);
        window.removeEventListener("orientationchange", refrescarDepois);
        screen.orientation?.removeEventListener("change", refrescarDepois);
        document.getElementById("video-foto")?.remove();
        gsap.ticker.remove(tick);
        passagem?.kill();
        ScrollTrigger.getAll().forEach((s) => s.kill());
        lenis.destroy();
        document.documentElement.classList.remove("js-anim");
        delete document.documentElement.dataset.motion;
      };
    })();

    return () => {
      vivo = false;
      limpar?.();
    };
  }, []);

  return null;
}
