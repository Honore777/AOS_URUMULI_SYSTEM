"""
Solver debug endpoints (protected by `copper` blueprint auth).

Allows an authenticated accountant to verify presence/version
of solver binaries (`highs`, `cbc`) in the runtime image.
"""
from utils import safe_jsonify
import shutil
import subprocess
import os
import logging
from copper import copper_bp

logger = logging.getLogger(__name__)


@copper_bp.route('/solvers_debug', methods=['GET'])
def solvers_debug():
    """Return JSON with `which` and quick `--version` output for known solvers.

    Protected by the `copper_bp.before_request` guard (login + role_required('accountant')).
    """
    results = {}
    for cmd in ('highs', 'cbc'):
        path = shutil.which(cmd)
        info = {'path': path}
        if path:
            try:
                proc = subprocess.run([cmd, '--version'], capture_output=True, text=True, timeout=5)
                out = (proc.stdout or proc.stderr or '').strip()
                info['version'] = out
                info['returncode'] = proc.returncode
            except Exception as e:
                logger.exception('solvers_debug: running %s --version failed', cmd)
                info['error'] = str(e)
        results[cmd] = info

    results['OPTIMIZER_ALLOW_FALLBACK_TO_CBC'] = os.environ.get('OPTIMIZER_ALLOW_FALLBACK_TO_CBC', '0')
    return safe_jsonify(results)
