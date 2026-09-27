"""Check that this installation can generate, run and score DGF-Bench. Never prints API keys."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from dgf_bench import __version__

CAIRO_HELP = {
    'win32': 'install a Cairo runtime (for example the GTK3 runtime, or MSYS2 `pacman -S mingw-w64-ucrt-x86_64-cairo`) and add its bin directory to PATH',
    'darwin': 'run `brew install cairo`',
    'linux': 'install libcairo2 (Debian/Ubuntu: `sudo apt install libcairo2`)',
}


def checks():
    """Yield (name, status, detail); status is OK, WARN or FAIL."""
    ok = sys.version_info >= (3, 10)
    yield 'Python', 'OK' if ok else 'FAIL', sys.version.split()[0] + ('' if ok else ' (3.10 or later required)')
    yield 'dgf-bench', 'OK', f'{__version__} at {Path(__file__).resolve().parent}'
    try:
        import docx  # noqa: F401
        yield 'python-docx', 'OK', 'reads Word evidence'
    except ImportError as exc:
        yield 'python-docx', 'FAIL', str(exc)
    from dgf_bench.azure_icon_registry import AzureIconRegistry
    registry = AzureIconRegistry()
    if registry.available:
        yield 'Azure icons', 'OK', f'{len(registry.files)} icons at {registry.root}'
    else:
        yield 'Azure icons', 'FAIL', f'none found at {registry.root}; generated diagrams would differ from published datasets'
    from dgf_bench.selftest import rendering_available
    if rendering_available():
        yield 'Cairo rendering', 'OK', 'can generate architecture diagrams'
    else:
        platform = 'linux' if sys.platform.startswith('linux') else sys.platform
        yield ('Cairo rendering', 'WARN', 'unavailable: running and scoring existing datasets works, generating new dossiers '
               'does not. To enable it, ' + CAIRO_HELP.get(platform, 'install the Cairo graphics library'))
    env_file = Path.cwd() / '.env'
    in_file = env_file.is_file() and any(line.strip().startswith('OPENROUTER_API_KEY=') and 'REPLACE_ME' not in line
                                         for line in env_file.read_text(encoding='utf-8').splitlines())
    if os.environ.get('OPENROUTER_API_KEY', '').strip() or in_file:
        yield 'OpenRouter key', 'OK', 'found in ' + ('environment' if os.environ.get('OPENROUTER_API_KEY') else str(env_file))
    else:
        yield 'OpenRouter key', 'WARN', 'not set; needed only for model runs (`dgf-bench configure`)'
    try:
        with tempfile.TemporaryFile(dir=Path.cwd()):
            pass
        yield 'Working directory', 'OK', f'{Path.cwd()} is writable'
    except OSError as exc:
        yield 'Working directory', 'FAIL', f'{Path.cwd()} is not writable: {exc}'


def main(argv=None):
    argparse.ArgumentParser(prog='dgf-bench doctor', description=__doc__).parse_args(argv)
    failed = False
    for name, status, detail in checks():
        failed |= status == 'FAIL'
        print(f'[{status:<4}] {name}: {detail}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
