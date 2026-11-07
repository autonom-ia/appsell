import { criarLimite } from "./limite";

// 20 contagens por minuto por IP na rota do banner (#563).
export const limiteDoConsentimento = criarLimite(20, 60_000);
