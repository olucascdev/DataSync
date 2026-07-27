"""Lease persistente e protecao contra execucoes repetidas do coletor."""

import psycopg

JOB_NAME = "estoque_pdf"


def adquirir_lease(
    conn: psycopg.Connection,
    owner: str,
    lease_seconds: int,
) -> bool:
    """Tenta reservar o coletor para uma unica instancia."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.carla_sync_control
                (job_name, lease_owner, lease_until, last_started_at, updated_at)
            VALUES
                (%s, %s, NOW() + make_interval(secs => %s), NOW(), NOW())
            ON CONFLICT (job_name) DO UPDATE
            SET lease_owner = EXCLUDED.lease_owner,
                lease_until = EXCLUDED.lease_until,
                last_started_at = NOW(),
                updated_at = NOW()
            WHERE (
                carla_sync_control.lease_until IS NULL
                OR carla_sync_control.lease_until < NOW()
            )
            AND (
                carla_sync_control.blocked_until IS NULL
                OR carla_sync_control.blocked_until <= NOW()
            )
            RETURNING job_name
            """,
            (JOB_NAME, owner, lease_seconds),
        )
        return cur.fetchone() is not None


def liberar_lease(
    conn: psycopg.Connection,
    owner: str,
    *,
    success: bool,
    failure_threshold: int = 3,
    failure_cooldown_seconds: int = 21600,
) -> None:
    """Libera a reserva e registra o ultimo sucesso, quando aplicavel."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE public.carla_sync_control
            SET lease_owner = NULL,
                lease_until = NULL,
                last_success_at = CASE
                    WHEN %s THEN NOW()
                    ELSE last_success_at
                END,
                consecutive_failures = CASE
                    WHEN %s THEN 0
                    ELSE consecutive_failures + 1
                END,
                blocked_until = CASE
                    WHEN %s THEN NULL
                    WHEN consecutive_failures + 1 >= %s
                        THEN NOW() + make_interval(secs => %s)
                    ELSE blocked_until
                END,
                updated_at = NOW()
            WHERE job_name = %s
              AND lease_owner = %s
            """,
            (
                success,
                success,
                success,
                failure_threshold,
                failure_cooldown_seconds,
                JOB_NAME,
                owner,
            ),
        )


def pode_executar_sync_inicial(
    conn: psycopg.Connection,
    min_interval_seconds: int,
) -> bool:
    """Evita novo acesso imediato quando o container reinicia em loop."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT (
                (
                    last_started_at IS NULL
                    OR last_started_at
                       <= NOW() - make_interval(secs => %s)
                )
                AND (
                    blocked_until IS NULL
                    OR blocked_until <= NOW()
                )
            )
            FROM public.carla_sync_control
            WHERE job_name = %s
            """,
            (min_interval_seconds, JOB_NAME),
        )
        row = cur.fetchone()
        return True if row is None else bool(row[0])
