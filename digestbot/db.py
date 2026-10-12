"""SQLite: настройки подписчиков и отметка «сегодня уже отправлено»."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from .config import DEFAULT_CURRENCIES, DEFAULT_FEEDS, DEFAULT_SEND_TIME, DEFAULT_TZ

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    city           TEXT NOT NULL DEFAULT '',
    lat            REAL,
    lon            REAL,
    tz             TEXT NOT NULL,
    currencies     TEXT NOT NULL,       -- коды через запятую
    feeds          TEXT NOT NULL,       -- id источников через запятую
    send_time      TEXT NOT NULL,       -- HH:MM по времени города
    enabled        INTEGER NOT NULL DEFAULT 1,
    last_sent_date TEXT,                -- YYYY-MM-DD по местному времени
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_enabled ON users(enabled);
"""


@dataclass(frozen=True)
class UserSettings:
    user_id: int
    name: str
    city: str
    lat: float | None
    lon: float | None
    tz: str
    currencies: tuple[str, ...]
    feeds: tuple[str, ...]
    send_time: str
    enabled: bool
    last_sent_date: str | None

    @property
    def has_city(self) -> bool:
        return bool(self.city) and self.lat is not None and self.lon is not None


def _split(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.split(",") if part)


def _user(row: aiosqlite.Row) -> UserSettings:
    return UserSettings(
        user_id=row["user_id"], name=row["name"], city=row["city"], lat=row["lat"], lon=row["lon"],
        tz=row["tz"], currencies=_split(row["currencies"]), feeds=_split(row["feeds"]),
        send_time=row["send_time"], enabled=bool(row["enabled"]), last_sent_date=row["last_sent_date"])


class Database:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    @classmethod
    async def open(cls, path: Path | str) -> "Database":
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.executescript(SCHEMA)
        await conn.commit()
        return cls(conn)

    async def close(self) -> None:
        await self.conn.close()

    async def _update(self, user_id: int, assignments: str, params: tuple) -> None:
        await self.conn.execute(f"UPDATE users SET {assignments} WHERE user_id = ?", (*params, user_id))
        await self.conn.commit()

    async def ensure_user(self, user_id: int, name: str) -> UserSettings:
        """Создаёт подписчика с настройками по умолчанию или возвращает существующего."""
        await self.conn.execute(
            "INSERT INTO users(user_id, name, tz, currencies, feeds, send_time, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET name = excluded.name",
            (user_id, name, DEFAULT_TZ, ",".join(DEFAULT_CURRENCIES), ",".join(DEFAULT_FEEDS),
             DEFAULT_SEND_TIME, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        await self.conn.commit()
        return await self.get_user(user_id)

    async def get_user(self, user_id: int) -> UserSettings | None:
        cur = await self.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return _user(row) if row else None

    async def enabled_users(self) -> list[UserSettings]:
        cur = await self.conn.execute("SELECT * FROM users WHERE enabled = 1 ORDER BY user_id")
        return [_user(row) for row in await cur.fetchall()]

    async def set_city(self, user_id: int, city: str, lat: float, lon: float, tz: str) -> None:
        await self._update(user_id, "city = ?, lat = ?, lon = ?, tz = ?, last_sent_date = NULL",
                           (city, lat, lon, tz))

    async def set_currencies(self, user_id: int, codes: tuple[str, ...]) -> None:
        await self._update(user_id, "currencies = ?", (",".join(codes),))

    async def set_feeds(self, user_id: int, feed_ids: tuple[str, ...]) -> None:
        await self._update(user_id, "feeds = ?", (",".join(feed_ids),))

    async def set_send_time(self, user_id: int, send_time: str) -> None:
        # новое время должно сработать даже если сегодняшний дайджест уже уходил
        await self._update(user_id, "send_time = ?, last_sent_date = NULL", (send_time,))

    async def set_enabled(self, user_id: int, enabled: bool) -> None:
        await self._update(user_id, "enabled = ?", (int(enabled),))

    async def mark_sent(self, user_id: int, local_date: str) -> None:
        await self._update(user_id, "last_sent_date = ?", (local_date,))
