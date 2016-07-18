# This file is part of the clcache project.
#
# The contents of this file are subject to the BSD 3-Clause License, the
# full text of which is available in the accompanying LICENSE file at the
# root directory of this project.
#
from distutils.core import setup
import py2exe

setup(
    version = "3.1.2-dev",
    description = "A compiler cache for Microsoft Visual Studio.",
    name = "CLCache",
    console = ["clcache.py"],
    options = {"py2exe": {"optimize": 2,
                          "bundle_files": 1,
                          "compressed": True}},
    zipfile = None
)
