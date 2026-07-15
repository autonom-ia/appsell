# EasyPanel — `sell-autonomia`

Este diretório documenta o hardening pós-deploy dos serviços exclusivos da
Autonom.ia Sell. Ele nunca opera serviços dos projetos `dados` ou `flow`.

## Invariante

- PostgreSQL, Redis e MinIO usam somente `easypanel-sell-autonomia`;
- nenhum deles publica portas, domínio ou rota Traefik;
- todos têm limites/reservas de CPU e memória;
- todos têm healthcheck contínuo e precisam estar `healthy`;
- alterações feitas pelo EasyPanel podem recriar a rede e o healthcheck, então
  o guard deve rodar depois de cada atualização pelo painel.
- o artefato de configuração da release inclui `easypanel/` com os dois scripts;
  a aplicação do hardening deve usar esse par proveniente do mesmo SHA aprovado.

## Verificação read-only

```bash
sudo deploy/easypanel/verify-sell-isolation.sh
```

## Reaplicação idempotente

Esta ação reinicia somente os três serviços novos do projeto Sell. Ela exige
confirmação explícita, valida os três serviços e o verificador antes da primeira
mutação, e termina executando a verificação read-only com valores exatos de
CPU, memória e healthcheck. Serviços já conformes não são reiniciados; quando
há drift, o comando só retorna sucesso depois de `UpdateStatus=completed`, troca
da task e saúde confirmada na task nova.

```bash
sudo env CONFIRM_SELL_AUTONOMIA_HARDENING=sell-autonomia-v1 \
  deploy/easypanel/harden-sell-services.sh
```

O EasyPanel continua sendo a fonte de configuração dos serviços. O script
existe apenas porque a versão 2.31 conecta bancos/apps à rede global e não
persiste healthchecks no modelo retornado pela API.
