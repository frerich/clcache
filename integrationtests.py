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
from contextlib import contextmanager
import copy
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import clcache


PYTHON_BINARY = sys.executable
CLCACHE_SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "clcache.py")
ASSETS_DIR = os.path.join("tests", "integrationtests")

if "CLCACHE_CMD" in os.environ:
    CLCACHE_CMD = os.environ['CLCACHE_CMD'].split()
else:
    CLCACHE_CMD = [PYTHON_BINARY, CLCACHE_SCRIPT]


@contextmanager
def cd(targetDirectory):
    oldDirectory = os.getcwd()
    os.chdir(os.path.expanduser(targetDirectory))
    try:
        yield
    finally:
        os.chdir(oldDirectory)


class TestCommandLineArguments(unittest.TestCase):
    def testValidMaxSize(self):
        with tempfile.TemporaryDirectory() as tempDir:
            customEnv = dict(os.environ, CLCACHE_DIR=tempDir)
            validValues = ["1", "  10", "42  ", "22222222"]
            for value in validValues:
                cmd = CLCACHE_CMD + ["-M", value]
                self.assertEqual(
                    subprocess.call(cmd, env=customEnv),
                    0,
                    "Command must not fail for max size: '" + value + "'")

    def testInvalidMaxSize(self):
        invalidValues = ["ababa", "-1", "0", "1000.0"]
        for value in invalidValues:
            cmd = CLCACHE_CMD + ["-M", value]
            self.assertNotEqual(subprocess.call(cmd), 0, "Command must fail for max size: '" + value + "'")


class TestCompileRuns(unittest.TestCase):
    def testBasicCompileCc(self):
        cmd = CLCACHE_CMD + ["/nologo", "/c", os.path.join(ASSETS_DIR, "fibonacci.c")]
        subprocess.check_call(cmd)

    def testBasicCompileCpp(self):
        cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c", os.path.join(ASSETS_DIR, "fibonacci.cpp")]
        subprocess.check_call(cmd)

    def testCompileLinkRunCc(self):
        with cd(ASSETS_DIR):
            cmd = CLCACHE_CMD + ["/nologo", "/c", "fibonacci.c", "/Fofibonacci_c.obj"]
            subprocess.check_call(cmd)
            cmd = ["link", "/nologo", "/OUT:fibonacci_c.exe", "fibonacci_c.obj"]
            subprocess.check_call(cmd)
            cmd = ["fibonacci_c.exe"]
            output = subprocess.check_output(cmd).decode("ascii").strip()
            self.assertEqual(output, "0 1 1 2 3 5 8 13 21 34 55 89 144 233 377")

    def testCompileLinkRunCpp(self):
        with cd(ASSETS_DIR):
            cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c", "fibonacci.cpp", "/Fofibonacci_cpp.obj"]
            subprocess.check_call(cmd)
            cmd = ["link", "/nologo", "/OUT:fibonacci_cpp.exe", "fibonacci_cpp.obj"]
            subprocess.check_call(cmd)
            cmd = ["fibonacci_cpp.exe"]
            output = subprocess.check_output(cmd).decode("ascii").strip()
            self.assertEqual(output, "0 1 1 2 3 5 8 13 21 34 55 89 144 233 377")

    def testRecompile(self):
        cmd = CLCACHE_CMD + [
            "/nologo",
            "/EHsc",
            "/c",
            os.path.join(ASSETS_DIR, "recompile1.cpp")
        ]
        subprocess.check_call(cmd) # Compile once
        subprocess.check_call(cmd) # Compile again

    def testRecompileObjectSetSameDir(self):
        cmd = CLCACHE_CMD + [
            "/nologo",
            "/EHsc",
            "/c",
            os.path.join(ASSETS_DIR, "recompile2.cpp"),
            "/Forecompile2_custom_object_name.obj"
        ]
        subprocess.check_call(cmd) # Compile once
        subprocess.check_call(cmd) # Compile again

    def testRecompileObjectSetOtherDir(self):
        cmd = CLCACHE_CMD + [
            "/nologo",
            "/EHsc",
            "/c",
            os.path.join(ASSETS_DIR, "recompile3.cpp"),
            "/Fotests\\output\\recompile2_custom_object_name.obj"
        ]
        subprocess.check_call(cmd) # Compile once
        subprocess.check_call(cmd) # Compile again

    def testPipedOutput(self):
        def debugLinebreaks(text):
            out = []
            lines = text.splitlines(True)
            for line in lines:
                out.append(line.replace("\r", "<CR>").replace("\n", "<LN>"))
            return "\n".join(out)

        commands = [
            # just show cl.exe version
            {
                'directMode': True,
                'compileFails': False,
                'cmd': CLCACHE_CMD
            },
            # passed to real compiler
            {
                'directMode': True,
                'compileFails': False,
                'cmd': CLCACHE_CMD + ['/E', 'fibonacci.c']
            },
            # Unique parameters ensure this was not cached yet (at least in CI)
            {
                'directMode': True,
                'compileFails': False,
                'cmd': CLCACHE_CMD + ['/wd4267', '/wo4018', '/c', 'fibonacci.c']
            },
            # Cache hit
            {
                'directMode': True,
                'compileFails': False,
                'cmd': CLCACHE_CMD + ['/wd4267', '/wo4018', '/c', 'fibonacci.c']
            },
            # Unique parameters ensure this was not cached yet (at least in CI)
            {
                'directMode': False,
                'compileFails': False,
                'cmd': CLCACHE_CMD + ['/wd4269', '/wo4019', '/c', 'fibonacci.c']
            },
            # Cache hit
            {
                'directMode': False,
                'compileFails': False,
                'cmd': CLCACHE_CMD + ['/wd4269', '/wo4019', '/c', 'fibonacci.c']
            },
            # Compile fails in NODIRECT mode. This will trigger a preprocessor fail via
            # cl.exe /EP /w1NONNUMERIC fibonacci.c
            {
                'directMode': False,
                'compileFails': True,
                'cmd': CLCACHE_CMD + ['/w1NONNUMERIC', '/c', 'fibonacci.c']
            },
        ]

        for command in commands:
            with cd(ASSETS_DIR):
                if command['directMode']:
                    testEnvironment = dict(os.environ)
                else:
                    testEnvironment = dict(os.environ, CLCACHE_NODIRECT="1")

                proc = subprocess.Popen(command['cmd'], env=testEnvironment,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdoutBinary, stderrBinary = proc.communicate()
                stdout = stdoutBinary.decode(clcache.CL_DEFAULT_CODEC)
                stderr = stderrBinary.decode(clcache.CL_DEFAULT_CODEC)

                if not command['compileFails'] and proc.returncode != 0:
                    self.fail(
                        'Compile failed with return code {}.\n'.format(proc.returncode) +
                        'Command: {}\nEnvironment: {}\nStdout: {}\nStderr: {}'.format(
                            command['cmd'], testEnvironment, stdout, stderr))

                if command['compileFails'] and proc.returncode == 0:
                    self.fail('Compile was expected to fail but did not. {}'.format(command['cmd']))

                for output in [stdout, stderr]:
                    if output:
                        self.assertTrue('\r\r\n' not in output,
                                        'Output has duplicated CR.\nCommand: {}\nOutput: {}'.format(
                                            command['cmd'], debugLinebreaks(output)))
                        # Just to be sure we have newlines
                        self.assertTrue('\r\n' in output,
                                        'Output has no CRLF.\nCommand: {}\nOutput: {}'.format(
                                            command['cmd'], debugLinebreaks(output)))


class TestCompilerEncoding(unittest.TestCase):
    def testNonAsciiMessage(self):
        with cd(os.path.join(ASSETS_DIR, "compiler-encoding")):
            for filename in ['non-ascii-message-ansi.c', 'non-ascii-message-utf16.c']:
                cmd = CLCACHE_CMD + ["/nologo", "/c", filename]
                subprocess.check_call(cmd)


class TestHits(unittest.TestCase):
    def testHitsSimple(self):
        with cd(os.path.join(ASSETS_DIR, "hits-and-misses")):
            cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c", 'hit.cpp']
            subprocess.check_call(cmd) # Ensure it has been compiled before

            cache = clcache.Cache()
            with cache.statistics as stats:
                oldHits = stats.numCacheHits()
            subprocess.check_call(cmd) # This must hit now
            with cache.statistics as stats:
                newHits = stats.numCacheHits()
            self.assertEqual(newHits, oldHits + 1)


class TestPrecompiledHeaders(unittest.TestCase):
    def testSampleproject(self):
        with cd(os.path.join(ASSETS_DIR, "precompiled-headers")):
            cpp = ' '.join(CLCACHE_CMD)

            testEnvironment = dict(os.environ, CPP=cpp)

            cmd = ["nmake", "/nologo"]
            subprocess.check_call(cmd, env=testEnvironment)

            cmd = ["myapp.exe"]
            subprocess.check_call(cmd)

            cmd = ["nmake", "/nologo", "clean"]
            subprocess.check_call(cmd, env=testEnvironment)

            cmd = ["nmake", "/nologo"]
            subprocess.check_call(cmd, env=testEnvironment)


class TestHeaderChange(unittest.TestCase):
    def _clean(self):
        if os.path.isfile("main.obj"):
            os.remove("main.obj")
        if os.path.isfile("main.exe"):
            os.remove("main.exe")

    def _compileAndLink(self, environment=None):
        cmdCompile = CLCACHE_CMD + ["/nologo", "/EHsc", "/c", "main.cpp"]
        cmdLink = ["link", "/nologo", "/OUT:main.exe", "main.obj"]
        subprocess.check_call(cmdCompile, env=environment or os.environ)
        subprocess.check_call(cmdLink, env=environment or os.environ)

    def testDirect(self):
        with cd(os.path.join(ASSETS_DIR, "header-change")):
            self._clean()

            with open("version.h", "w") as header:
                header.write("#define VERSION 1")

            self._compileAndLink()
            cmdRun = [os.path.abspath("main.exe")]
            output = subprocess.check_output(cmdRun).decode("ascii").strip()
            self.assertEqual(output, "1")

            self._clean()

            with open("version.h", "w") as header:
                header.write("#define VERSION 2")

            self._compileAndLink()
            cmdRun = [os.path.abspath("main.exe")]
            output = subprocess.check_output(cmdRun).decode("ascii").strip()
            self.assertEqual(output, "2")

    def testNoDirect(self):
        with cd(os.path.join(ASSETS_DIR, "header-change")):
            self._clean()

            with open("version.h", "w") as header:
                header.write("#define VERSION 1")

            testEnvironment = dict(os.environ, CLCACHE_NODIRECT="1")

            self._compileAndLink(testEnvironment)
            cmdRun = [os.path.abspath("main.exe")]
            output = subprocess.check_output(cmdRun).decode("ascii").strip()
            self.assertEqual(output, "1")

            self._clean()

            with open("version.h", "w") as header:
                header.write("#define VERSION 2")

            self._compileAndLink(testEnvironment)
            cmdRun = [os.path.abspath("main.exe")]
            output = subprocess.check_output(cmdRun).decode("ascii").strip()
            self.assertEqual(output, "2")


class TestRunParallel(unittest.TestCase):
    def _zeroStats(self):
        subprocess.check_call(CLCACHE_CMD + ["-z"])

    def _buildAll(self):
        processes = []

        for sourceFile in glob.glob('*.cpp'):
            print("Starting compilation of {}".format(sourceFile))
            cxxflags = ["/c", "/nologo", "/EHsc"]
            cmd = CLCACHE_CMD + cxxflags + [sourceFile]
            processes.append(subprocess.Popen(cmd))

        for p in processes:
            p.wait()

    # Test counting of misses and hits in a parallel environment
    def testParallel(self):
        with cd(os.path.join(ASSETS_DIR, "parallel")):
            self._zeroStats()

            # Compile first time
            self._buildAll()

            cache = clcache.Cache()
            with cache.statistics as stats:
                hits = stats.numCacheHits()
                misses = stats.numCacheMisses()
            self.assertEqual(hits + misses, 10)

            # Compile second time
            self._buildAll()

            cache = clcache.Cache()
            with cache.statistics as stats:
                hits = stats.numCacheHits()
                misses = stats.numCacheMisses()
            self.assertEqual(hits + misses, 20)

    def testHitViaMpSequential(self):
        with cd(os.path.join(ASSETS_DIR, "parallel")), tempfile.TemporaryDirectory() as tempDir:
            cache = clcache.Cache(tempDir)

            customEnv = dict(os.environ, CLCACHE_DIR=tempDir)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 0)
                self.assertEqual(stats.numCacheEntries(), 0)

            cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c"]

            # Compile random file, filling cache
            subprocess.check_call(cmd + ["fibonacci01.cpp"], env=customEnv)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 1)
                self.assertEqual(stats.numCacheEntries(), 1)

            # Compile same files with specifying /MP, this should hit
            subprocess.check_call(cmd + ["/MP", "fibonacci01.cpp"], env=customEnv)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 1)
                self.assertEqual(stats.numCacheMisses(), 1)
                self.assertEqual(stats.numCacheEntries(), 1)

    def testHitsViaMpConcurrent(self):
        with cd(os.path.join(ASSETS_DIR, "parallel")), tempfile.TemporaryDirectory() as tempDir:
            cache = clcache.Cache(tempDir)

            customEnv = dict(os.environ, CLCACHE_DIR=tempDir)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 0)
                self.assertEqual(stats.numCacheEntries(), 0)

            cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c"]

            # Compile two random files
            subprocess.check_call(cmd + ["fibonacci01.cpp"], env=customEnv)
            subprocess.check_call(cmd + ["fibonacci02.cpp"], env=customEnv)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 2)
                self.assertEqual(stats.numCacheEntries(), 2)

            # Compile same two files concurrently, this should hit twice.
            subprocess.check_call(cmd + ["/MP2", "fibonacci01.cpp", "fibonacci02.cpp"], env=customEnv)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 2)
                self.assertEqual(stats.numCacheMisses(), 2)
                self.assertEqual(stats.numCacheEntries(), 2)


class TestClearing(unittest.TestCase):
    def _clearCache(self):
        subprocess.check_call(CLCACHE_CMD + ["-C"])

    def testClearIdempotency(self):
        cache = clcache.Cache()

        self._clearCache()
        with cache.statistics as stats:
            self.assertEqual(stats.currentCacheSize(), 0)
            self.assertEqual(stats.numCacheEntries(), 0)

        # Clearing should be idempotent
        self._clearCache()
        with cache.statistics as stats:
            self.assertEqual(stats.currentCacheSize(), 0)
            self.assertEqual(stats.numCacheEntries(), 0)

    def testClearPostcondition(self):
        cache = clcache.Cache()

        # Compile a random file to populate cache
        cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c", os.path.join(ASSETS_DIR, "fibonacci.cpp")]
        subprocess.check_call(cmd)

        # Now there should be something in the cache
        with cache.statistics as stats:
            self.assertTrue(stats.currentCacheSize() > 0)
            self.assertTrue(stats.numCacheEntries() > 0)

        # Now, clear the cache: the stats should remain unchanged except for
        # the cache size and number of cache entries.
        oldStats = copy.copy(cache.statistics)
        self._clearCache()
        with cache.statistics as stats:
            self.assertEqual(stats.currentCacheSize(), 0)
            self.assertEqual(stats.numCacheEntries(), 0)
            self.assertEqual(stats.numCallsWithoutSourceFile(), oldStats.numCallsWithoutSourceFile())
            self.assertEqual(stats.numCallsWithMultipleSourceFiles(), oldStats.numCallsWithMultipleSourceFiles())
            self.assertEqual(stats.numCallsWithPch(), oldStats.numCallsWithPch())
            self.assertEqual(stats.numCallsForLinking(), oldStats.numCallsForLinking())
            self.assertEqual(stats.numCallsForPreprocessing(), oldStats.numCallsForPreprocessing())
            self.assertEqual(stats.numCallsForExternalDebugInfo(), oldStats.numCallsForExternalDebugInfo())
            self.assertEqual(stats.numEvictedMisses(), oldStats.numEvictedMisses())
            self.assertEqual(stats.numHeaderChangedMisses(), oldStats.numHeaderChangedMisses())
            self.assertEqual(stats.numSourceChangedMisses(), oldStats.numSourceChangedMisses())
            self.assertEqual(stats.numCacheHits(), oldStats.numCacheHits())
            self.assertEqual(stats.numCacheMisses(), oldStats.numCacheMisses())


class TestAnalysisErrorsCalls(unittest.TestCase):
    def testAllKnownAnalysisErrors(self):
        # This ensures all AnalysisError cases are run once without crashes

        with cd(os.path.join(ASSETS_DIR)):
            baseCmd = [PYTHON_BINARY, CLCACHE_SCRIPT, '/nologo']

            # NoSourceFileError
            # This must fail because cl.exe: "cl : Command line error D8003 : missing source filename"
            # Make sure it was cl.exe that failed and not clcache
            process = subprocess.Popen(baseCmd + [], stderr=subprocess.PIPE)
            _, stderr = process.communicate()
            self.assertEqual(process.returncode, 2)
            self.assertTrue("D8003" in stderr.decode(clcache.CL_DEFAULT_CODEC))

            # InvalidArgumentError
            # This must fail because cl.exe: "cl : Command line error D8004 : '/Zm' requires an argument"
            # Make sure it was cl.exe that failed and not clcache
            process = subprocess.Popen(baseCmd + ['/c', '/Zm', 'bar', "minimal.cpp"], stderr=subprocess.PIPE)
            _, stderr = process.communicate()
            self.assertEqual(process.returncode, 2)
            self.assertTrue("D8004" in stderr.decode(clcache.CL_DEFAULT_CODEC))

            # MultipleSourceFilesComplexError
            subprocess.check_call(baseCmd + ['/c', '/Tcfibonacci.c', "minimal.cpp"])
            # CalledForLinkError
            subprocess.check_call(baseCmd + ["fibonacci.cpp"])
            # CalledWithPchError
            subprocess.check_call(baseCmd + ['/c', '/Yc', "minimal.cpp"])
            # ExternalDebugInfoError
            subprocess.check_call(baseCmd + ['/c', '/Zi', "minimal.cpp"])
            # CalledForPreprocessingError
            subprocess.check_call(baseCmd + ['/E', "minimal.cpp"])


class TestPreprocessorCalls(unittest.TestCase):
    def testHitsSimple(self):
        invocations = [
            ["/nologo", "/E"],
            ["/nologo", "/EP", "/c"],
            ["/nologo", "/P", "/c"],
            ["/nologo", "/E", "/EP"],
        ]

        cache = clcache.Cache()
        with cache.statistics as stats:
            oldPreprocessorCalls = stats.numCallsForPreprocessing()

        for i, invocation in enumerate(invocations, 1):
            cmd = CLCACHE_CMD + invocation + [os.path.join(ASSETS_DIR, "minimal.cpp")]
            subprocess.check_call(cmd)
            with cache.statistics as stats:
                newPreprocessorCalls = stats.numCallsForPreprocessing()
            self.assertEqual(newPreprocessorCalls, oldPreprocessorCalls + i, str(cmd))


class TestNoDirectCalls(unittest.TestCase):
    def testPreprocessorFailure(self):
        cache = clcache.Cache()

        oldStats = copy.copy(cache.statistics)

        cmd = CLCACHE_CMD + ["/nologo", "/c", "doesnotexist.cpp"]
        env = dict(os.environ, CLCACHE_NODIRECT="1")

        self.assertNotEqual(subprocess.call(cmd, env=env), 0)

        self.assertEqual(cache.statistics, oldStats)

    def testHit(self):
        with cd(os.path.join(ASSETS_DIR, "hits-and-misses")):
            cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c", "hit.cpp"]
            env = dict(os.environ, CLCACHE_NODIRECT="1")

            self.assertEqual(subprocess.call(cmd, env=env), 0)

            cache = clcache.Cache()
            with cache.statistics as stats:
                oldHits = stats.numCacheHits()

            self.assertEqual(subprocess.call(cmd, env=env), 0) # This should hit now
            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), oldHits + 1)


    def testHitViaMpSequential(self):
        with cd(os.path.join(ASSETS_DIR, "parallel")), tempfile.TemporaryDirectory() as tempDir:
            cache = clcache.Cache(tempDir)

            customEnv = dict(os.environ, CLCACHE_DIR=tempDir, CLCACHE_NODIRECT="1")

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 0)
                self.assertEqual(stats.numCacheEntries(), 0)

            cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c"]

            # Compile random file, filling cache
            subprocess.check_call(cmd + ["fibonacci01.cpp"], env=customEnv)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 1)
                self.assertEqual(stats.numCacheEntries(), 1)

            # Compile same files with specifying /MP, this should hit
            subprocess.check_call(cmd + ["/MP", "fibonacci01.cpp"], env=customEnv)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 1)
                self.assertEqual(stats.numCacheMisses(), 1)
                self.assertEqual(stats.numCacheEntries(), 1)

    def testHitsViaMpConcurrent(self):
        with cd(os.path.join(ASSETS_DIR, "parallel")), tempfile.TemporaryDirectory() as tempDir:
            cache = clcache.Cache(tempDir)

            customEnv = dict(os.environ, CLCACHE_DIR=tempDir, CLCACHE_NODIRECT="1")

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 0)
                self.assertEqual(stats.numCacheEntries(), 0)

            cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c"]

            # Compile two random files
            subprocess.check_call(cmd + ["fibonacci01.cpp"], env=customEnv)
            subprocess.check_call(cmd + ["fibonacci02.cpp"], env=customEnv)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 0)
                self.assertEqual(stats.numCacheMisses(), 2)
                self.assertEqual(stats.numCacheEntries(), 2)

            # Compile same two files concurrently, this should hit twice.
            subprocess.check_call(cmd + ["/MP2", "fibonacci01.cpp", "fibonacci02.cpp"], env=customEnv)

            with cache.statistics as stats:
                self.assertEqual(stats.numCacheHits(), 2)
                self.assertEqual(stats.numCacheMisses(), 2)
                self.assertEqual(stats.numCacheEntries(), 2)


class TestBasedir(unittest.TestCase):
    def testBasedir(self):
        with cd(os.path.join(ASSETS_DIR, "basedir")), tempfile.TemporaryDirectory() as tempDir:
            # First, create two separate build directories with the same sources
            for buildDir in ["builddir_a", "builddir_b"]:
                shutil.rmtree(buildDir, ignore_errors=True)
                os.mkdir(buildDir)

                shutil.copy("main.cpp", buildDir)
                shutil.copy("constants.h", buildDir)

            cache = clcache.Cache(tempDir)

            cmd = CLCACHE_CMD + ["/nologo", "/EHsc", "/c", "main.cpp"]

            # Build once in one directory
            with cd("builddir_a"):
                env = dict(os.environ, CLCACHE_DIR=tempDir, CLCACHE_BASEDIR=os.getcwd())
                self.assertEqual(subprocess.call(cmd, env=env), 0)
                with cache.statistics as stats:
                    self.assertEqual(stats.numCacheMisses(), 1)
                    self.assertEqual(stats.numCacheHits(), 0)

            shutil.rmtree("builddir_a", ignore_errors=True)

            # Build again in a different directory, this should hit now because of CLCACHE_BASEDIR
            with cd("builddir_b"):
                env = dict(os.environ, CLCACHE_DIR=tempDir, CLCACHE_BASEDIR=os.getcwd())
                self.assertEqual(subprocess.call(cmd, env=env), 0)
                with cache.statistics as stats:
                    self.assertEqual(stats.numCacheMisses(), 1)
                    self.assertEqual(stats.numCacheHits(), 1)


if __name__ == '__main__':
    unittest.TestCase.longMessage = True
    unittest.main()
