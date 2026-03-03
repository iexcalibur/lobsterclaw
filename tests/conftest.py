"""
Test configuration: set required env vars before any module imports.
This prevents Config.validate() from raising ValueError in unit tests.
"""
import os

# Minimal required env vars so Config.validate() passes
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_OWNER_ID"] = "123456789"
os.environ["LLM_PROVIDER"] = "anthropic"
os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"
os.environ["EXEC_ENABLED"] = "true"

# Reset config cache so tests get fresh config with the above env vars
try:
    import config as _cfg_mod
    _cfg_mod._config = None
except ImportError:
    pass

# Node test fixtures
os.environ.setdefault("NODE_0_NAME", "testpi")
os.environ.setdefault("NODE_0_HOST", "192.168.1.99")
os.environ.setdefault("NODE_0_USER", "pi")
os.environ.setdefault("NODE_0_MAC", "aa:bb:cc:dd:ee:ff")
