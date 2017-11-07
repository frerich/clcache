#!/usr/bin/env python

import os

from setuptools import setup, find_packages

setup(
    name='clcache',
    description='MSVC compiler cache',
    author='Frerich Raabe',
    author_email='raabe@froglogic.com',
    url='https://github.com/frerich/clcache',
    packages=find_packages(),
    platforms='any',
    keywords=[],
    install_requires=[
        'typing; python_version < "3.5"',
        'subprocess.run; python_version < "3.5"',
        'pymemcache',
        'pyuv',
    ],
    entry_points={
          'console_scripts': [
              'clcache = clcache.__main__:main',
              'clcache-server = clcache.server.__main__:main',
          ]
    },
    setup_requires=[
        'setuptools_scm',
    ],
    data_files=[
        ('', ('clcache.pth',)),
    ],
    use_scm_version=True)
