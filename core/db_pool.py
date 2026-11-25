"""Small thread-safe PostgreSQL connection pool for hot request paths."""

import threading
from typing import Dict

from psycopg2.pool import ThreadedConnectionPool


_POOLS: Dict[str, ThreadedConnectionPool] = {}
_LOCK = threading.Lock()


def pooled_connect(conn_str: str):
    """Return a close-compatible pooled connection proxy."""
    with _LOCK:
        pool = _POOLS.get(conn_str)
        if pool is None:
            pool = ThreadedConnectionPool(1, 12, conn_str)
            _POOLS[conn_str] = pool
    return _PooledConnection(pool, pool.getconn())


class _PooledConnection:
    def __init__(self, pool: ThreadedConnectionPool, conn):
        self._pool = pool
        self._conn = conn
        self._returned = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        if self._returned:
            return
        self._returned = True
        try:
            self._conn.rollback()
        finally:
            self._pool.putconn(self._conn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
