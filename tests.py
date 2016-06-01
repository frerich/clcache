import clcache
from contextlib import contextmanager
import multiprocessing
import os
import subprocess
import sys
import unittest

@contextmanager
def cd(target_directory):
    old_directory = os.getcwd()
    os.chdir(os.path.expanduser(target_directory))
    try:
        yield
    finally:
        os.chdir(old_directory)

class BaseTest(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        self.longMessage = True
        super(BaseTest, self).__init__(*args, **kwargs)

PYTHON_BINARY = sys.executable
CLCACHE_SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "clcache.py")

class TestExtractArgument(BaseTest):
    def testSimple(self):
        # Keep
        self.assertEqual(clcache.extractArgument(r''), r'')
        self.assertEqual(clcache.extractArgument(r'1'), r'1')
        self.assertEqual(clcache.extractArgument(r'myfile.cpp'), r'myfile.cpp')
        self.assertEqual(clcache.extractArgument(r'/DEXTERNAL_DLL=__declspec(dllexport)'), r'/DEXTERNAL_DLL=__declspec(dllexport)')
        self.assertEqual(clcache.extractArgument(r'-DVERSION=\\"1.0\\"'), r'-DVERSION=\\"1.0\\"')
        self.assertEqual(clcache.extractArgument(r'-I"..\.."'), r'-I"..\.."')

        # Extract
        self.assertEqual(clcache.extractArgument(r'"-IC:\Program Files\Lib1"'),
                                                 r'-IC:\Program Files\Lib1')
        self.assertEqual(clcache.extractArgument(r'"/Fo"CrashReport.dir\Release\""'),
                                                 r'/Fo"CrashReport.dir\Release\"')
        self.assertEqual(clcache.extractArgument(r'"-DWEBRTC_SVNREVISION=\"Unavailable(issue687)\""'),
                                                 r'-DWEBRTC_SVNREVISION=\"Unavailable(issue687)\"')

class TestSplitCommandsFile(BaseTest):
    def _genericTest(self, fileContents, expectedOutput):
        splitted = clcache.splitCommandsFile(fileContents)
        self.assertEqual(splitted, expectedOutput)

    def testWhitespace(self):
        self._genericTest('-A -B    -C', ['-A', '-B', '-C'])
        self._genericTest('   -A -B -C', ['-A', '-B', '-C'])
        self._genericTest('-A -B -C   ', ['-A', '-B', '-C'])

    def testSlashes(self):
        self._genericTest(r'-A -I..\..   -C',
                          ['-A', r'-I..\..', '-C'])

    def testDubleQuotes(self):
        self._genericTest(r'"-IC:\Program Files\Lib1" "-IC:\Program Files\Lib2" -I"..\.."',
                          [r'-IC:\Program Files\Lib1', r'-IC:\Program Files\Lib2', r'-I"..\.."'])

    def testEscapedQuotes(self):
        self._genericTest(r'"-DWEBRTC_SVNREVISION=\"Unavailable(issue687)\"" -D_WIN32_WINNT=0x0602',
                          [r'-DWEBRTC_SVNREVISION=\"Unavailable(issue687)\"', '-D_WIN32_WINNT=0x0602'])

    def testLineEndings(self):
        self._genericTest('-A\n-B', ['-A', '-B'])
        self._genericTest('-A\r\n-B', ['-A', '-B'])
        self._genericTest('-A -B\r\n-C -D -E', ['-A', '-B', '-C', '-D', '-E'])

class TestParseIncludes(BaseTest):
    def _readSampleFileDefault(self, lang=None):
        if lang == "de":
            filePath = r'tests\parse-includes\compiler_output_lang_de.txt'
            uniqueIncludesCount = 82
        else:
            filePath = r'tests\parse-includes\compiler_output.txt'
            uniqueIncludesCount = 83

        with open(filePath, 'r') as infile:
            return {
                'CompilerOutput': infile.read(),
                'UniqueIncludesCount': uniqueIncludesCount
            }

    def _readSampleFileNoIncludes(self):
        with open(r'tests\parse-includes\compiler_output_no_includes.txt', 'r') as infile:
            return {
                'CompilerOutput': infile.read(),
                'UniqueIncludesCount': 0
            }

    def testParseIncludesNoStrip(self):
        sample = self._readSampleFileDefault()
        includesSet, newCompilerOutput = clcache.parseIncludesList(sample['CompilerOutput'],
            r"C:\Users\me\test\smartsqlite\src\version.cpp", None, strip=False)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\users\me\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)
        self.assertEqual(newCompilerOutput, sample['CompilerOutput'])

    def testParseIncludesStrip(self):
        sample = self._readSampleFileDefault()
        includesSet, newCompilerOutput = clcache.parseIncludesList(sample['CompilerOutput'],
            r"C:\Users\me\test\smartsqlite\src\version.cpp", None, strip=True)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\users\me\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)
        self.assertEqual(newCompilerOutput, "version.cpp\n")

    def testParseIncludesNoIncludes(self):
        sample = self._readSampleFileNoIncludes()
        for stripIncludes in [True, False]:
            includesSet, newCompilerOutput = clcache.parseIncludesList(sample['CompilerOutput'],
                r"C:\Users\me\test\myproject\main.cpp", None,
                strip=stripIncludes)

            self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
            self.assertEqual(newCompilerOutput, "main.cpp\n")

    def testParseIncludesGerman(self):
        sample = self._readSampleFileDefault(lang="de")
        includesSet, newCompilerOutput = clcache.parseIncludesList(sample['CompilerOutput'],
            r"C:\Users\me\test\smartsqlite\src\version.cpp", None, strip=False)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\users\me\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)

class TestMultipleSourceFiles(BaseTest):
    CPU_CORES = multiprocessing.cpu_count()

    def testCpuCuresPlausibility(self):
        # 1 <= CPU_CORES <= 32
        self.assertGreaterEqual(self.CPU_CORES, 1)
        self.assertLessEqual(self.CPU_CORES, 32)

    def testJobCount(self):
        # Basic parsing
        actual = clcache.jobCount(["/MP1"])
        self.assertEqual(actual, 1)
        actual = clcache.jobCount(["/MP100"])
        self.assertEqual(actual, 100)

        # Without optional max process value
        actual = clcache.jobCount(["/MP"])
        self.assertEqual(actual, self.CPU_CORES)

        # Invalid inputs
        actual = clcache.jobCount(["/MP100.0"])
        self.assertEqual(actual, 1)
        actual = clcache.jobCount(["/MP-100"])
        self.assertEqual(actual, 1)
        actual = clcache.jobCount(["/MPfoo"])
        self.assertEqual(actual, 1)

        # Multiple values
        actual = clcache.jobCount(["/MP1", "/MP44"])
        self.assertEqual(actual, 44)
        actual = clcache.jobCount(["/MP1", "/MP44", "/MP"])
        self.assertEqual(actual, self.CPU_CORES)

        # Find /MP in mixed command line
        actual = clcache.jobCount(["/c", "/nologo", "/MP44"])
        self.assertEqual(actual, 44)
        actual = clcache.jobCount(["/c", "/nologo", "/MP44", "mysource.cpp"])
        self.assertEqual(actual, 44)
        actual = clcache.jobCount(["/MP2", "/c", "/nologo", "/MP44", "mysource.cpp"])
        self.assertEqual(actual, 44)
        actual = clcache.jobCount(["/MP2", "/c", "/MP44", "/nologo", "/MP", "mysource.cpp"])
        self.assertEqual(actual, self.CPU_CORES)

class TestCommandLineArguments(BaseTest):
    @unittest.skip("Do not run this test by default because it change user's cache settings")
    def testValidMaxSize(self):
        valid_values = ["1", "  10", "42  ", "22222222"]
        for value in valid_values:
            cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "-M", value]
            self.assertEqual(subprocess.call(cmd), 0, "Command must not fail for max size: '" + value + "'")

    def testInvalidMaxSize(self):
        invalid_values = ["ababa", "-1", "0", "1000.0"]
        for value in invalid_values:
            cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "-M", value]
            self.assertNotEqual(subprocess.call(cmd), 0, "Command must fail for max size: '" + value + "'")

class TestCompileRuns(BaseTest):
    def testBasicCompileC(self):
        cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "/nologo", "/c", "tests\\fibonacci.c"]
        subprocess.check_call(cmd)

    def testBasicCompileCpp(self):
        cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "/nologo", "/EHsc", "/c", "tests\\fibonacci.cpp"]
        subprocess.check_call(cmd)

    def testCompileLinkRunC(self):
        cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "/nologo", "/c", "tests\\fibonacci.c", "/Fofibonacci_c.obj"]
        subprocess.check_call(cmd)
        cmd = ["link", "/nologo", "/OUT:fibonacci_c.exe", "fibonacci_c.obj"]
        subprocess.check_call(cmd)
        cmd = ["fibonacci_c.exe"]
        output = subprocess.check_output(cmd).decode("ascii").strip()
        self.assertEqual(output, "0 1 1 2 3 5 8 13 21 34 55 89 144 233 377")

    def testCompileLinkRunCpp(self):
        cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "/nologo", "/EHsc", "/c", "tests\\fibonacci.cpp", "/Fofibonacci_cpp.obj"]
        subprocess.check_call(cmd)
        cmd = ["link", "/nologo", "/OUT:fibonacci_cpp.exe", "fibonacci_cpp.obj"]
        subprocess.check_call(cmd)
        cmd = ["fibonacci_cpp.exe"]
        output = subprocess.check_output(cmd).decode("ascii").strip()
        self.assertEqual(output, "0 1 1 2 3 5 8 13 21 34 55 89 144 233 377")

    def testRecompile(self):
        cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "/nologo", "/EHsc", "/c", "tests\\recompile1.cpp"]
        subprocess.check_call(cmd) # Compile once
        subprocess.check_call(cmd) # Compile again

    def testRecompileObjectSetSameDir(self):
        cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "/nologo", "/EHsc", "/c", "tests\\recompile2.cpp", "/Forecompile2_custom_object_name.obj"]
        subprocess.check_call(cmd) # Compile once
        subprocess.check_call(cmd) # Compile again

    def testRecompileObjectSetOtherDir(self):
        cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "/nologo", "/EHsc", "/c", "tests\\recompile3.cpp", "/Fotests\\output\\recompile2_custom_object_name.obj"]
        subprocess.check_call(cmd) # Compile once
        subprocess.check_call(cmd) # Compile again

class TestHits(BaseTest):
    def testHitsSimple(self):
        cmd = [PYTHON_BINARY, CLCACHE_SCRIPT, "/nologo", "/EHsc", "/c", r'tests\hits-and-misses\hit.cpp']
        subprocess.check_call(cmd) # Ensure it has been compiled before

        cache = clcache.ObjectCache()
        oldHits = clcache.CacheStatistics(cache).numCacheHits()
        subprocess.check_call(cmd) # This must hit now
        newHits = clcache.CacheStatistics(cache).numCacheHits()
        self.assertEqual(newHits, oldHits + 1)

class TestPrecompiledHeaders(BaseTest):
    def testSampleproject(self):
        with cd(os.path.join("tests", "precompiled-headers")):
            cpp = PYTHON_BINARY + " " + CLCACHE_SCRIPT

            cmd = ["nmake", "/nologo"]
            subprocess.check_call(cmd, env=dict(os.environ, CPP=cpp))

            cmd = ["myapp.exe"]
            subprocess.check_call(cmd)

            cmd = ["nmake", "/nologo", "clean"]
            subprocess.check_call(cmd, env=dict(os.environ, CPP=cpp))

            cmd = ["nmake", "/nologo"]
            subprocess.check_call(cmd, env=dict(os.environ, CPP=cpp))

class TestHeaderChange(BaseTest):
    def _clean(self):
        if os.path.isfile("main.obj"):
            os.remove("main.obj")
        if os.path.isfile("main.exe"):
            os.remove("main.exe")

    def _compileAndLink(self, environment = os.environ):
        cmdCompile = [PYTHON_BINARY, CLCACHE_SCRIPT, "/nologo", "/EHsc", "/c", "main.cpp"]
        cmdLink = ["link", "/nologo", "/OUT:main.exe", "main.obj"]
        subprocess.check_call(cmdCompile, env=environment)
        subprocess.check_call(cmdLink, env=environment)

    def testDirect(self):
        with cd(os.path.join("tests", "header-change")):
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
        with cd(os.path.join("tests", "header-change")):
            self._clean()

            with open("version.h", "w") as header:
                header.write("#define VERSION 1")

            self._compileAndLink(dict(os.environ, CLCACHE_NODIRECT="1"))
            cmdRun = [os.path.abspath("main.exe")]
            output = subprocess.check_output(cmdRun).decode("ascii").strip()
            self.assertEqual(output, "1")

            self._clean()

            with open("version.h", "w") as header:
                header.write("#define VERSION 2")

            self._compileAndLink(dict(os.environ, CLCACHE_NODIRECT="1"))
            cmdRun = [os.path.abspath("main.exe")]
            output = subprocess.check_output(cmdRun).decode("ascii").strip()
            self.assertEqual(output, "2")

if __name__ == '__main__':
    unittest.main()
