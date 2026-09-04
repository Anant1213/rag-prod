from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from app.config import settings

_pool: AsyncConnectionPool | None = None


async def _configure(conn) -> None:
    await register_vector_async(conn)


async def open_pool() -> AsyncConnectionPool:
    global _pool
    _pool = AsyncConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=10,
        open=False,
        configure=_configure,
        timeout=10,
    )
    await _pool.open(wait=True, timeout=15)
    return _pool


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("pool not initialised")
    return _pool


async def ping() -> bool:
    try:
        async with pool().connection() as conn:
            cur = await conn.execute("select 1")
            await cur.fetchone()
        return True
    except Exception:  # noqa: BLE001 - readiness must report false, never raise
        return False
