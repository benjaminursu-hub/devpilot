"""One application logger, used by the API and the worker.

Uvicorn configures only its own loggers, so without this our errors would print without
timestamps or levels (or not at all below WARNING).
"""

import logging
import sys

logger = logging.getLogger("devpilot")

if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
