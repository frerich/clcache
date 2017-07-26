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
    py_modules=['clcache'],
    entry_points={
          'console_scripts': [
              'clcache = clcache:main'
          ]
    },
    install_requires=['pypiwin32'],
    setup_requires=[
        'setuptools_scm',
    ],
    use_scm_version=True)
