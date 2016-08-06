#!/usr/bin/env python
#
# This file is part of the clcache project.
#
# The contents of this file are subject to the BSD 3-Clause License, the
# full text of which is available in the accompanying LICENSE file at the
# root directory of this project.
#
# In Python unittests are always members, not functions. Silence lint in this file.
# pylint: disable=no-self-use
#
from multiprocessing import cpu_count
import os
import shutil
import subprocess
import sys
import tempfile
import timeit
import unittest

import clcache

PYTHON_BINARY = sys.executable
CLCACHE_SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "clcache.py")
ASSETS_DIR = os.path.join("tests", "performancetests")

if "CLCACHE_CMD" in os.environ:
    CLCACHE_CMD = os.environ['CLCACHE_CMD'].split()
else:
    CLCACHE_CMD = [PYTHON_BINARY, CLCACHE_SCRIPT]

def takeTime(code):
    start = timeit.default_timer()
    code()
    return timeit.default_timer() - start

class TestConcurrency(unittest.TestCase):
    NUM_SOURCE_FILES = 30

    def testConcurrentHitsScaling(self):
        for i in range(1, TestConcurrency.NUM_SOURCE_FILES):
            shutil.copyfile(
                os.path.join(ASSETS_DIR, 'concurrency', 'file01.cpp'),
                os.path.join(ASSETS_DIR, 'concurrency', 'file{:02d}.cpp'.format(i+1))
            )

        sources = []
        for i in range(1, TestConcurrency.NUM_SOURCE_FILES+1):
            sources.append(os.path.join(ASSETS_DIR, 'concurrency', 'file{:02d}.cpp'.format(i)))

        with tempfile.TemporaryDirectory() as tempDir:
            customEnv = dict(os.environ, CLCACHE_DIR=tempDir)

            cache = clcache.Cache(tempDir)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 0)
                self.assertEqual(stats.numCacheEntries(), 0)

            # Populate cache
            cmd = CLCACHE_CMD + ['/nologo', '/EHsc', '/c'] + sources
            coldCacheSequential = takeTime(lambda: subprocess.check_call(cmd, env=customEnv))

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), len(sources))
                self.assertEqual(stats.numCacheEntries(), len(sources))

            # Compile one-by-one, measuring the time.
            cmd = CLCACHE_CMD + ['/nologo', '/EHsc', '/c'] + sources
            hotCacheSequential = takeTime(lambda: subprocess.check_call(cmd, env=customEnv))

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), len(sources))
                self.assertEqual(stats.numCacheMisses(), len(sources))
                self.assertEqual(stats.numCacheEntries(), len(sources))

            # Recompile with many concurrent processes, measuring time
            cmd = CLCACHE_CMD + ['/nologo', '/EHsc', '/c', '/MP{}'.format(cpu_count())] + sources
            hotCacheConcurrent = takeTime(lambda: subprocess.check_call(cmd, env=customEnv))

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), len(sources) * 2)
                self.assertEqual(stats.numCacheMisses(), len(sources))
                self.assertEqual(stats.numCacheEntries(), len(sources))

            print("Compiling {} source files sequentially, cold cache: {} seconds"
                  .format(len(sources), coldCacheSequential))
            print("Compiling {} source files sequentially, hot cache: {} seconds"
                  .format(len(sources), hotCacheSequential))
            print("Compiling {} source files concurrently via /MP{}, hot cache: {} seconds"
                  .format(len(sources), cpu_count(), hotCacheConcurrent))


if __name__ == '__main__':
    unittest.TestCase.longMessage = True
    unittest.main()
