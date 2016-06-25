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
