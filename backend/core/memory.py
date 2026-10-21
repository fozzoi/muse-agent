import os
import re
import json
import base64
import uuid
import shutil
import mimetypes
from pathlib import Path

MEMORY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "sessions")
UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")
os.makedirs(MEMORY_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

def extract_image_path(output: str) -> str | None:
    """Extracts any image path referenced in tool JSON output, output_dir, or plain string."""
    if not output:
        return None
    try:
        data = json.loads(output)
        if isinstance(data, dict):
            for k in ("file_path", "image_path", "path", "file", "dest_file"):
                val = data.get(k)
                if val and isinstance(val, str):
                    exp = os.path.expanduser(val.strip())
                    if os.path.isfile(exp) and exp.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        return exp

            # Check if output_dir with image files was returned
            out_dir = data.get("output_dir")
            if out_dir and isinstance(out_dir, str):
                exp_dir = os.path.expanduser(out_dir.strip())
                if os.path.isdir(exp_dir):
                    # Check files key first
                    flist = data.get("files", [])
                    if isinstance(flist, list):
                        for fn in flist:
                            fp = os.path.join(exp_dir, str(fn))
                            if os.path.isfile(fp) and fp.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                                return fp
                    # Check directory files
                    for fn in sorted(os.listdir(exp_dir)):
                        fp = os.path.join(exp_dir, fn)
                        if os.path.isfile(fp) and fp.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                            return fp
    except Exception:
        pass

    matches = re.findall(r'(?:[\'"]|(?<=\s)|^)((?:/[\w\.\-]+)+/(?:[\w\.\-]+\.(?:png|jpg|jpeg|webp)))', output, re.IGNORECASE)
    for m in matches:
        exp = os.path.expanduser(m.strip())
        if os.path.isfile(exp):
            return exp

    matches_home = re.findall(r'(?:[\'"]|(?<=\s)|^)(~/(?:[\w\.\-]+/)*(?:[\w\.\-]+\.(?:png|jpg|jpeg|webp)))', output, re.IGNORECASE)
    for m in matches_home:
        exp = os.path.expanduser(m.strip())
        if os.path.isfile(exp):
            return exp

    return None

def copy_file_to_uploads(filepath: str) -> str:
    """Copies a local image file to UPLOADS_DIR and returns its relative /api/uploads/ URL."""
    try:
        if not filepath or not os.path.isfile(filepath):
            return ""
        ext = os.path.splitext(filepath)[1].lower() or ".png"
        filename = f"{uuid.uuid4().hex[:12]}{ext}"
        dest = os.path.join(UPLOADS_DIR, filename)
        shutil.copyfile(filepath, dest)
        return f"/api/uploads/{filename}"
    except Exception as e:
        print(f"[Memory Warning] Could not copy file to uploads: {e}")
        return ""

def save_upload_image(b64_data: str, mime_type: str = "image/jpeg") -> str:
    """Decodes and stores an uploaded image, returning its relative API path."""
    try:
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
        ext = mimetypes.guess_extension(mime_type) or ".jpg"
        if ext in (".jpe", ".jpeg"):
            ext = ".jpg"
        filename = f"{uuid.uuid4().hex[:12]}{ext}"
        filepath = os.path.join(UPLOADS_DIR, filename)
        img_bytes = base64.b64decode(b64_data)
        with open(filepath, "wb") as f:
            f.write(img_bytes)
        return f"/api/uploads/{filename}"
    except Exception as e:
        print(f"[Memory Warning] Could not save upload image: {e}")
        return ""

def _get_session_file(session_id: str) -> str:
    safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_")) or "default"
    return os.path.join(MEMORY_DIR, f"{safe_id}.json")

def load_session(session_id: str) -> list[dict]:
    filepath = _get_session_file(session_id)
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Memory Warning] Could not read session {session_id}: {e}")
            return []
    return []

def save_session(session_id: str, history: list[dict]):
    filepath = _get_session_file(session_id)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[Memory Warning] Could not save session {session_id}: {e}")

def add_message(session_id: str, role: str, content: str, extra: dict | None = None):
    history = load_session(session_id)
    msg = {"role": role, "content": content}
    if extra:
        msg.update(extra)
    history.append(msg)
    # Retain up to 40 recent messages per session for context depth
    if len(history) > 40:
        history = history[-40:]
    save_session(session_id, history)

def clear_session(session_id: str):
    filepath = _get_session_file(session_id)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            pass

def get_history(session_id: str, limit: int = 10) -> list[dict]:
    history = load_session(session_id)
    return history[-limit:]

def format_history_context(session_id: str, limit: int = 8) -> str:
    history = load_session(session_id)
    recent = history[-limit:]
    if not recent:
        return "None"
    lines = []
    for msg in recent:
        role = msg.get("role", "user").upper()
        content = msg.get("content", "").strip()
        lines.append(f"{role}: {content}")
    return "\n".join(lines)
