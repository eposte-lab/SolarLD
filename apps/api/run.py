"""Railway entry point.

Reads PORT from environment (Railway injects it dynamically) before
starting uvicorn. This avoids the shell-expansion problem: railway.toml
startCommand runs in exec-mode (no shell), so '$PORT' would be passed
literally to uvicorn. Python reads os.environ directly.
"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("src.main:app", host="0.0.0.0", port=port, log_level="info")
