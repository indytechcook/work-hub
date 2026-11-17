"""Keep unit tests independent of the developer's BB vault and private state."""
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import settings


def isolate_storage(case):
    folder = tempfile.TemporaryDirectory()
    case.addCleanup(folder.cleanup)
    env = {k: v for k, v in os.environ.items() if not k.startswith('WORK_HUB_')}
    env['WORK_HUB_STORAGE'] = 'json'
    replacement = patch.dict(os.environ, env, clear=True)
    replacement.start()
    case.addCleanup(replacement.stop)
    replacement = patch.object(settings, 'STATE', Path(folder.name))
    replacement.start()
    case.addCleanup(replacement.stop)
