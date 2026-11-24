"""
SSH GPU instance settings — the fields live in
``app/core/settings/gpu_instances.py``; this alias keeps ``cfg.GPU_INSTANCE_*``
call sites short.
"""

from app.core.logger import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

gpu_instance_settings = settings
