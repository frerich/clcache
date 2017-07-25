#!/usr/bin/env python

import os

from setuptools import setup, find_packages

setup(
    name='clcache',
    description='MSVC Complier cache',
    author='frerich',
    author_email='frerich@users.noreply.github.com',
    url='https://github.com/frerich/clcache',
    packages=find_packages(),
    platforms='any',
    keywords=[],
    install_requires=['pypiwin32'],
    setup_requires=[
        'setuptools_scm',
    ],
    use_scm_version=True)
