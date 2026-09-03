from typing import Any
try:
    from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
except ImportError:
    AsyncIOMotorClient = None
    AsyncIOMotorDatabase = None

from core.config import MONGO_CONNECTION_STRING, MONGO_DATABASE_NAME

class MongoConnection:
    def __init__(self, uri: str | None, database_name: str):
        self.uri = uri
        self.database_name = database_name
        self.client: AsyncIOMotorClient | None = None
        self.db: AsyncIOMotorDatabase | None = None
        self.error: str | None = None

    async def connect(self) -> None:
        if AsyncIOMotorClient is None:
            self.error = "MongoDB driver missing. Run: pip install -r requirements.txt"
            return

        if not self.uri:
            self.error = "MongoDB connection string not found in .env"
            return

        try:
            self.client = AsyncIOMotorClient(self.uri, serverSelectionTimeoutMS=5000)
            self.db = self.client[self.database_name]
            await self.client.admin.command("ping")
            self.error = None
        except Exception as exc:
            self.error = str(exc)

    async def close(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
            self.db = None

    async def status(self) -> dict[str, Any]:
        if not self.client:
            return {"connected": False, "database": self.database_name, "error": self.error}
        try:
            await self.client.admin.command("ping")
            return {"connected": True, "database": self.database_name, "error": None}
        except Exception as exc:
            self.error = str(exc)
            return {"connected": False, "database": self.database_name, "error": self.error}

mongo = MongoConnection(MONGO_CONNECTION_STRING, MONGO_DATABASE_NAME)
