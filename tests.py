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

class TestExtractArgument(unittest.TestCase):
    def testSimple(self):
        # Keep
        self.assertEqual(clcache.extractArgument(r''), r'')
        self.assertEqual(clcache.extractArgument(r'1'), r'1')
        self.assertEqual(clcache.extractArgument(r'myfile.cpp'), r'myfile.cpp')
        self.assertEqual(clcache.extractArgument(r'-DVERSION=\\"1.0\\"'), r'-DVERSION=\\"1.0\\"')
        self.assertEqual(clcache.extractArgument(r'-I"..\.."'), r'-I"..\.."')

        # Extract
        self.assertEqual(clcache.extractArgument(r'"-IC:\Program Files\Lib1"'),
                                                 r'-IC:\Program Files\Lib1')
        self.assertEqual(clcache.extractArgument(r'"/Fo"CrashReport.dir\Release\""'),
                                                 r'/Fo"CrashReport.dir\Release\"')
        self.assertEqual(clcache.extractArgument(r'"-DWEBRTC_SVNREVISION=\"Unavailable(issue687)\""'),
                                                 r'-DWEBRTC_SVNREVISION=\"Unavailable(issue687)\"')


class TestSplitCommandsFile(unittest.TestCase):
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

class TestMultipleSourceFiles(unittest.TestCase):
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

class TestCompileRuns(unittest.TestCase):
    PYTHON_BINARY = sys.executable

    def testBasicCompileC(self):
        cmd = [self.PYTHON_BINARY, "clcache.py", "/nologo", "/c", "tests\\fibonacci.c"]
        subprocess.check_call(cmd)

    def testBasicCompileCpp(self):
        cmd = [self.PYTHON_BINARY, "clcache.py", "/nologo", "/EHsc", "/c", "tests\\fibonacci.cpp"]
        subprocess.check_call(cmd)

    def testCompileLinkRunC(self):
        cmd = [self.PYTHON_BINARY, "clcache.py", "/nologo", "/c", "tests\\fibonacci.c", "/Fofibonacci_c.obj"]
        subprocess.check_call(cmd)
        cmd = ["link", "/nologo", "/OUT:fibonacci_c.exe", "fibonacci_c.obj"]
        subprocess.check_call(cmd)
        cmd = ["fibonacci_c.exe"]
        output = subprocess.check_output(cmd).decode("ascii").strip()
        self.assertEqual(output, "0 1 1 2 3 5 8 13 21 34 55 89 144 233 377")

    def testCompileLinkRunCpp(self):
        cmd = [self.PYTHON_BINARY, "clcache.py", "/nologo", "/EHsc", "/c", "tests\\fibonacci.cpp", "/Fofibonacci_cpp.obj"]
        subprocess.check_call(cmd)
        cmd = ["link", "/nologo", "/OUT:fibonacci_cpp.exe", "fibonacci_cpp.obj"]
        subprocess.check_call(cmd)
        cmd = ["fibonacci_cpp.exe"]
        output = subprocess.check_output(cmd).decode("ascii").strip()
        self.assertEqual(output, "0 1 1 2 3 5 8 13 21 34 55 89 144 233 377")

    def testRecompile(self):
        cmd = [self.PYTHON_BINARY, "clcache.py", "/nologo", "/EHsc", "/c", "tests\\recompile1.cpp"]
        subprocess.check_call(cmd) # Compile once
        subprocess.check_call(cmd) # Compile again

    def testRecompileObjectSetSameDir(self):
        cmd = [self.PYTHON_BINARY, "clcache.py", "/nologo", "/EHsc", "/c", "tests\\recompile2.cpp", "/Forecompile2_custom_object_name.obj"]
        subprocess.check_call(cmd) # Compile once
        subprocess.check_call(cmd) # Compile again

    def testRecompileObjectSetOtherDir(self):
        cmd = [self.PYTHON_BINARY, "clcache.py", "/nologo", "/EHsc", "/c", "tests\\recompile3.cpp", "/Fotests\\output\\recompile2_custom_object_name.obj"]
        subprocess.check_call(cmd) # Compile once
        subprocess.check_call(cmd) # Compile again

class TestPrecompiledHeaders(unittest.TestCase):
    PYTHON_BINARY = sys.executable
    CLCACHE_SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "clcache.py")

    def testSampleproject(self):
        with cd(os.path.join("tests", "precompiled-headers")):
            cpp = self.PYTHON_BINARY + " " + self.CLCACHE_SCRIPT

            cmd = ["nmake", "/nologo"]
            subprocess.check_call(cmd, env=dict(os.environ, CPP=cpp))

            cmd = ["myapp.exe"]
            subprocess.check_call(cmd)

            cmd = ["nmake", "/nologo", "clean"]
            subprocess.check_call(cmd, env=dict(os.environ, CPP=cpp))

            cmd = ["nmake", "/nologo"]
            subprocess.check_call(cmd, env=dict(os.environ, CPP=cpp))

if __name__ == '__main__':
    unittest.main()
