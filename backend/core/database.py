import os
import json
from motor.motor_asyncio import AsyncIOMotorClient
from core.config import Config

MANIFEST_PATH = os.path.join(Config.TOOLS_DIR, "manifest.json")

def _get_mongo_collection():
    uri = Config.MONGO_URI
    if uri and not uri.endswith("..."):
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        return client["muse_agent"]["tools"]
    return None

def load_local_manifest() -> dict:
    if os.path.exists(MANIFEST_PATH):
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_local_manifest(manifest: dict):
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

async def save_tool(name: str, description: str, code: str):
    # 1. Write Python file locally
    file_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(code)

    # 2. Update local manifest.json
    manifest = load_local_manifest()
    manifest[name] = {"description": description}
    save_local_manifest(manifest)

    # 3. Sync to MongoDB Atlas if configured
    col = _get_mongo_collection()
    if col is not None:
        try:
            await col.update_one(
                {"name": name},
                {"$set": {"name": name, "description": description, "code": code}},
                upsert=True,
            )
        except Exception as e:
            print(f"[DB Warning] MongoDB sync skipped: {e}")

async def sync_tools_from_cloud() -> dict:
    """Pulls saved tools from MongoDB into local tools/ folder on startup."""
    manifest = load_local_manifest()
    col = _get_mongo_collection()
    if col is not None:
        try:
            async for doc in col.find({}):
                name = doc["name"]
                file_path = os.path.join(Config.TOOLS_DIR, f"{name}.py")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(doc["code"])
                manifest[name] = {"description": doc["description"]}
            save_local_manifest(manifest)
        except Exception as e:
            print(f"[DB Warning] Cloud pull skipped: {e}")
    return manifest
