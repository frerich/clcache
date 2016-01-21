import unittest
import clcache
import subprocess
import sys

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

if __name__ == '__main__':
    unittest.main()
