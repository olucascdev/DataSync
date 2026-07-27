BEGIN;

ALTER TABLE public.carla_produtos
    ADD COLUMN IF NOT EXISTS codigo_erp text,
    ADD COLUMN IF NOT EXISTS preco_atualizado_em timestamp with time zone;

-- Preserva os precos atuais por 24 horas depois da migracao.
UPDATE public.carla_produtos
SET preco_atualizado_em = COALESCE(preco_atualizado_em, now());

CREATE UNIQUE INDEX IF NOT EXISTS carla_produtos_codigo_erp_uidx
    ON public.carla_produtos (codigo_erp)
    WHERE codigo_erp IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.carla_preco_divergencias (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    produto_id uuid NOT NULL,
    codigo_erp text NOT NULL,
    valor_atual numeric(10,2),
    valor_recebido numeric(10,2) NOT NULL,
    variacao_percentual numeric(12,4),
    status text DEFAULT 'pendente' NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone
);

CREATE UNIQUE INDEX IF NOT EXISTS carla_preco_divergencias_pendente_uidx
    ON public.carla_preco_divergencias (produto_id, valor_recebido)
    WHERE status = 'pendente';

CREATE TABLE IF NOT EXISTS public.carla_sync_control (
    job_name text PRIMARY KEY,
    lease_owner text,
    lease_until timestamp with time zone,
    last_started_at timestamp with time zone,
    last_success_at timestamp with time zone,
    consecutive_failures integer DEFAULT 0 NOT NULL,
    blocked_until timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.carla_sync_control
    ADD COLUMN IF NOT EXISTS consecutive_failures integer DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS blocked_until timestamp with time zone;

COMMIT;
