from __future__ import annotations

import os
import sys
from pathlib import Path


# The agent service settings are instantiated at import time and require DATABASE_URL.
# Unit tests don't need a real database, but they do need imports to succeed.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")

# Avoid accidental model config errors if any test imports codepaths that reference it.
os.environ.setdefault("OPENAI_API_KEY", "test-key")

# Ensure the monorepo root is importable (so `import services.*` works in tests).
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

