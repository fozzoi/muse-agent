import io
import mss
from PIL import Image

sct = mss.mss()


def grab_screen_frame(quality: int = 50) -> bytes:
  """Grabs the primary screen and returns compressed JPEG bytes."""
  monitor = sct.monitors[1]  # Primary monitor
  raw = sct.grab(monitor)
  img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

  # Downscale to 1280x720 for fast network transfer
  img.thumbnail((1280, 720))

  buf = io.BytesIO()
  img.save(buf, format="JPEG", quality=quality)
  return buf.getvalue()