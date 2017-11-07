import os
import sys
import shutil

from typing import List
from contextlib import suppress
from os.path import join, dirname, isfile

def patch_distutils() -> None:
    # Try to import numpy.distutils first so that we
    # can patch after numpy does (We're very early in
    # the import cycle)

    with suppress(ImportError):
        from numpy.distutils import ccompiler as _

    from distutils import ccompiler
    from clcache import __main__

    clcache_main = [sys.executable, __main__.__file__]
    ccompiler_spawn = ccompiler.CCompiler.spawn
    def msvc_compiler_spawn(self: ccompiler.CCompiler, cmd: List[str]) -> None:
        if not hasattr(self, 'cc'):  # type: ignore
            return ccompiler_spawn(self, cmd)

        if os.path.basename(self.cc) not in ['cl', 'cl.exe']:  # type: ignore
            return ccompiler_spawn(self, cmd)

        if cmd[0] != self.cc:  # type: ignore
            # We're not running the compiler
            return ccompiler_spawn(self, cmd)

        cmd = clcache_main + cmd[1:]
        # Set the environment variables so that clcache can run
        os.environ['CLCACHE_CL'] = self.cc  # type: ignore
        print('Note: patching distutils because $env:USE_CLCACHE is set')
        
        return ccompiler_spawn(self, cmd)

    ccompiler.CCompiler.spawn = msvc_compiler_spawn  # type: ignore


def main() -> None:
    if os.environ.get('USE_CLCACHE') != '1':
        return

    patch_distutils()
