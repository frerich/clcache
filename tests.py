import unittest
import clcache

class TestSplitCommandsFile(unittest.TestCase):
    def _genericTest(self, fileContents, expectedOutput):
        splitted = clcache.splitCommandsFile(fileContents)
        self.assertEqual(splitted, expectedOutput)

    def testBasic(self):
        self._genericTest('-A -B    -C',
                          ['-A', '-B', '-C'])

    def testSlashes(self):
        self._genericTest('-A -I..\\..   -C',
                          ['-A', '-I..\\..', '-C'])

    def testDubleQuotes(self):
        self._genericTest('"-IC:\\Program Files\\Lib1" "-IC:\\Program Files\\Lib2" -I"..\\.."',
                          ['-IC:\\Program Files\\Lib1', '-IC:\\Program Files\\Lib2', '-I"..\\.."'])


    def testEscapedQuotes(self):
        self._genericTest('"-DWEBRTC_SVNREVISION=\\"Unavailable(issue687)\\"" -D_WIN32_WINNT=0x0602',
                          ['-DWEBRTC_SVNREVISION="Unavailable(issue687)"', '-D_WIN32_WINNT=0x0602'])


if __name__ == '__main__':
    unittest.main()
