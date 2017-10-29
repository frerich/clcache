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

from clcache import __main__ as clcache

PYTHON_BINARY = sys.executable
CLCACHE_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "clcache", "clcache.py")
ASSETS_DIR = os.path.join(os.path.join(os.path.dirname(__file__), "performancetests"))

if "CLCACHE_CMD" in os.environ:
    CLCACHE_CMD = os.environ['CLCACHE_CMD'].split()
else:
    CLCACHE_CMD = ['clcache']

def takeTime(code):
    start = timeit.default_timer()
    code()
    return timeit.default_timer() - start

class TestConcurrency(unittest.TestCase):
    NUM_SOURCE_FILES = 30

    @classmethod
    def setUpClass(cls):
        for i in range(1, TestConcurrency.NUM_SOURCE_FILES):
            shutil.copyfile(
                os.path.join(ASSETS_DIR, 'concurrency', 'file01.cpp'),
                os.path.join(ASSETS_DIR, 'concurrency', 'file{:02d}.cpp'.format(i+1))
            )

        cls.sources = []
        for i in range(1, TestConcurrency.NUM_SOURCE_FILES+1):
            cls.sources.append(os.path.join(ASSETS_DIR, 'concurrency', 'file{:02d}.cpp'.format(i)))

    def testConcurrentHitsScaling(self):
        with tempfile.TemporaryDirectory() as tempDir:
            customEnv = dict(os.environ, CLCACHE_DIR=tempDir)

            cache = clcache.Cache(tempDir)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 0)
                self.assertEqual(stats.numCacheEntries(), 0)

            # Populate cache
            cmd = CLCACHE_CMD + ['/nologo', '/EHsc', '/c'] + TestConcurrency.sources
            coldCacheSequential = takeTime(lambda: subprocess.check_call(cmd, env=customEnv))

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), len(TestConcurrency.sources))
                self.assertEqual(stats.numCacheEntries(), len(TestConcurrency.sources))

            # Compile one-by-one, measuring the time.
            cmd = CLCACHE_CMD + ['/nologo', '/EHsc', '/c'] + TestConcurrency.sources
            hotCacheSequential = takeTime(lambda: subprocess.check_call(cmd, env=customEnv))

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), len(TestConcurrency.sources))
                self.assertEqual(stats.numCacheMisses(), len(TestConcurrency.sources))
                self.assertEqual(stats.numCacheEntries(), len(TestConcurrency.sources))

            # Recompile with many concurrent processes, measuring time
            cmd = CLCACHE_CMD + ['/nologo', '/EHsc', '/c', '/MP{}'.format(cpu_count())] + TestConcurrency.sources
            hotCacheConcurrent = takeTime(lambda: subprocess.check_call(cmd, env=customEnv))

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), len(TestConcurrency.sources) * 2)
                self.assertEqual(stats.numCacheMisses(), len(TestConcurrency.sources))
                self.assertEqual(stats.numCacheEntries(), len(TestConcurrency.sources))

            print("Compiling {} source files sequentially, cold cache: {} seconds"
                  .format(len(TestConcurrency.sources), coldCacheSequential))
            print("Compiling {} source files sequentially, hot cache: {} seconds"
                  .format(len(TestConcurrency.sources), hotCacheSequential))
            print("Compiling {} source files concurrently via /MP{}, hot cache: {} seconds"
                  .format(len(TestConcurrency.sources), cpu_count(), hotCacheConcurrent))


if __name__ == '__main__':
    unittest.TestCase.longMessage = True
    unittest.main()
