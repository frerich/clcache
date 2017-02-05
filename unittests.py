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
import multiprocessing
import os
import unittest
import tempfile

import clcache
from clcache import (
    CommandLineAnalyzer,
    CompilerArtifactsRepository,
    Configuration,
    Manifest,
    ManifestEntry,
    ManifestRepository,
    Statistics,
)
from clcache import (
    AnalysisError,
    CalledForLinkError,
    CalledForPreprocessingError,
    InvalidArgumentError,
    MultipleSourceFilesComplexError,
    NoSourceFileError,
    PersistentJSONDict,
)
from storage import CacheMemcacheStrategy

ASSETS_DIR = os.path.join("tests", "unittests")


@contextmanager
def cd(targetDirectory):
    oldDirectory = os.getcwd()
    os.chdir(os.path.expanduser(targetDirectory))
    try:
        yield
    finally:
        os.chdir(oldDirectory)


def temporaryFileName():
    with tempfile.NamedTemporaryFile() as f:
        return f.name


class TestHelperFunctions(unittest.TestCase):
    def testBasenameWithoutExtension(self):
        self.assertEqual(clcache.basenameWithoutExtension(r"README.asciidoc"), "README")
        self.assertEqual(clcache.basenameWithoutExtension(r"/home/user/README.asciidoc"), "README")
        self.assertEqual(clcache.basenameWithoutExtension(r"C:\Project\README.asciidoc"), "README")

        self.assertEqual(clcache.basenameWithoutExtension(r"READ ME.asciidoc"), "READ ME")
        self.assertEqual(clcache.basenameWithoutExtension(r"/home/user/READ ME.asciidoc"), "READ ME")
        self.assertEqual(clcache.basenameWithoutExtension(r"C:\Project\READ ME.asciidoc"), "READ ME")

        self.assertEqual(clcache.basenameWithoutExtension(r"README.asciidoc.tmp"), "README.asciidoc")
        self.assertEqual(clcache.basenameWithoutExtension(r"/home/user/README.asciidoc.tmp"), "README.asciidoc")
        self.assertEqual(clcache.basenameWithoutExtension(r"C:\Project\README.asciidoc.tmp"), "README.asciidoc")

    def testNormalizeBaseDir(self):
        self.assertIsNone(clcache.normalizeBaseDir(None))
        self.assertIsNone(clcache.normalizeBaseDir(r""))

        # Note: raw string literals cannot end in an odd number of backslashes
        # https://docs.python.org/3/faq/design.html#why-can-t-raw-strings-r-strings-end-with-a-backslash
        # So we consistenly use basic literals
        self.assertEqual(clcache.normalizeBaseDir("c:"), "c:")
        self.assertEqual(clcache.normalizeBaseDir("c:\\projects"), "c:\\projects")

        self.assertEqual(clcache.normalizeBaseDir("C:\\"), "c:")
        self.assertEqual(clcache.normalizeBaseDir("C:\\Projects\\"), "c:\\projects")

        self.assertEqual(clcache.normalizeBaseDir("c:\\projects with space"), "c:\\projects with space")
        self.assertEqual(clcache.normalizeBaseDir("c:\\projects with ö"), "c:\\projects with ö")

    def testFilesBeneathSimple(self):
        with cd(os.path.join(ASSETS_DIR, "files-beneath")):
            files = list(clcache.filesBeneath("a"))
            self.assertEqual(len(files), 2)
            self.assertIn(r"a\1.txt", files)
            self.assertIn(r"a\2.txt", files)

    def testFilesBeneathDeep(self):
        with cd(os.path.join(ASSETS_DIR, "files-beneath")):
            files = list(clcache.filesBeneath("b"))
            self.assertEqual(len(files), 1)
            self.assertIn(r"b\c\3.txt", files)

    def testFilesBeneathRecursive(self):
        with cd(os.path.join(ASSETS_DIR, "files-beneath")):
            files = list(clcache.filesBeneath("."))
            self.assertEqual(len(files), 5)
            self.assertIn(r".\a\1.txt", files)
            self.assertIn(r".\a\2.txt", files)
            self.assertIn(r".\b\c\3.txt", files)
            self.assertIn(r".\d\4.txt", files)
            self.assertIn(r".\d\e\5.txt", files)


class TestExtendCommandLineFromEnvironment(unittest.TestCase):
    def testEmpty(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment([], {})
        self.assertEqual(cmdLine, [])
        self.assertEqual(env, {})

    def testSimple(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment(['/nologo'], {'USER': 'ab'})
        self.assertEqual(cmdLine, ['/nologo'])
        self.assertEqual(env, {'USER': 'ab'})

    def testPrepend(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment(['/nologo'], {
            'USER': 'ab',
            'CL': '/MP',
        })
        self.assertEqual(cmdLine, ['/MP', '/nologo'])
        self.assertEqual(env, {'USER': 'ab'})

    def testPrependMultiple(self):
        cmdLine, _ = clcache.extendCommandLineFromEnvironment(['INPUT.C'], {
            'CL': r'/Zp2 /Ox /I\INCLUDE\MYINCLS \LIB\BINMODE.OBJ',
        })
        self.assertEqual(cmdLine, ['/Zp2', '/Ox', r'/I\INCLUDE\MYINCLS', r'\LIB\BINMODE.OBJ', 'INPUT.C'])

    def testAppend(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment(['/nologo'], {
            'USER': 'ab',
            '_CL_': 'file.c',
        })
        self.assertEqual(cmdLine, ['/nologo', 'file.c'])
        self.assertEqual(env, {'USER': 'ab'})

    def testAppendPrepend(self):
        cmdLine, env = clcache.extendCommandLineFromEnvironment(['/nologo'], {
            'USER': 'ab',
            'CL': '/MP',
            '_CL_': 'file.c',
        })
        self.assertEqual(cmdLine, ['/MP', '/nologo', 'file.c'])
        self.assertEqual(env, {'USER': 'ab'})


class TestConfiguration(unittest.TestCase):
    def testOpenClose(self):
        with Configuration(temporaryFileName()):
            pass

    def testDefaults(self):
        with Configuration(temporaryFileName()) as cfg:
            self.assertGreaterEqual(cfg.maximumCacheSize(), 1024) # 1KiB


class TestStatistics(unittest.TestCase):
    def testOpenClose(self):
        with Statistics(temporaryFileName()):
            pass

    def testHitCounts(self):
        with Statistics(temporaryFileName()) as s:
            self.assertEqual(s.numCallsWithInvalidArgument(), 0)
            self.assertEqual(s.numCallsWithoutSourceFile(), 0)
            self.assertEqual(s.numCallsWithMultipleSourceFiles(), 0)
            self.assertEqual(s.numCallsWithPch(), 0)
            self.assertEqual(s.numCallsForLinking(), 0)
            self.assertEqual(s.numCallsForExternalDebugInfo(), 0)
            self.assertEqual(s.numEvictedMisses(), 0)
            self.assertEqual(s.numHeaderChangedMisses(), 0)
            self.assertEqual(s.numSourceChangedMisses(), 0)
            self.assertEqual(s.numCacheHits(), 0)
            self.assertEqual(s.numCacheMisses(), 0)
            self.assertEqual(s.numCallsForPreprocessing(), 0)

            # Bump all by 1
            s.registerCallWithInvalidArgument()
            s.registerCallWithoutSourceFile()
            s.registerCallWithMultipleSourceFiles()
            s.registerCallWithPch()
            s.registerCallForLinking()
            s.registerCallForExternalDebugInfo()
            s.registerEvictedMiss()
            s.registerHeaderChangedMiss()
            s.registerSourceChangedMiss()
            s.registerCacheHit()
            s.registerCacheMiss()
            s.registerCallForPreprocessing()

            self.assertEqual(s.numCallsWithInvalidArgument(), 1)
            self.assertEqual(s.numCallsWithoutSourceFile(), 1)
            self.assertEqual(s.numCallsWithMultipleSourceFiles(), 1)
            self.assertEqual(s.numCallsWithPch(), 1)
            self.assertEqual(s.numCallsForLinking(), 1)
            self.assertEqual(s.numCallsForExternalDebugInfo(), 1)
            self.assertEqual(s.numEvictedMisses(), 1)
            self.assertEqual(s.numHeaderChangedMisses(), 1)
            self.assertEqual(s.numSourceChangedMisses(), 1)
            self.assertEqual(s.numCacheHits(), 1)
            self.assertEqual(s.numCallsForPreprocessing(), 1)

            # accumulated: headerChanged, sourceChanged, eviced, miss
            self.assertEqual(s.numCacheMisses(), 4)


class TestManifestRepository(unittest.TestCase):
    entry1 = ManifestEntry([r'somepath\myinclude.h'],
                           "fdde59862785f9f0ad6e661b9b5746b7",
                           "a649723940dc975ebd17167d29a532f8")
    entry2 = ManifestEntry([r'somepath\myinclude.h', r'moreincludes.h'],
                           "474e7fc26a592d84dfa7416c10f036c6",
                           "8771d7ebcf6c8bd57a3d6485f63e3a89")
    # Size in (120, 240] bytes
    manifest1 = Manifest([entry1])
    # Size in (120, 240] bytes
    manifest2 = Manifest([entry2])

    def _getDirectorySize(self, dirPath):
        def filesize(path, filename):
            return os.stat(os.path.join(path, filename)).st_size

        size = 0
        for path, _, filenames in clcache.WALK(dirPath):
            size += sum(filesize(path, f) for f in filenames)

        return size

    def testPaths(self):
        manifestsRootDir = os.path.join(ASSETS_DIR, "manifests")
        mm = ManifestRepository(manifestsRootDir)
        ms = mm.section("fdde59862785f9f0ad6e661b9b5746b7")

        self.assertEqual(ms.manifestSectionDir, os.path.join(manifestsRootDir, "fd"))
        self.assertEqual(ms.manifestPath("fdde59862785f9f0ad6e661b9b5746b7"),
                         os.path.join(manifestsRootDir, "fd", "fdde59862785f9f0ad6e661b9b5746b7.json"))

    def testIncludesContentHash(self):
        self.assertEqual(
            ManifestRepository.getIncludesContentHashForHashes([]),
            ManifestRepository.getIncludesContentHashForHashes([])
        )

        self.assertEqual(
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf"]),
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf"])
        )

        self.assertEqual(
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf", "f6c8bd5733"]),
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf", "f6c8bd5733"])
        )

        # Wrong number of elements
        self.assertNotEqual(
            ManifestRepository.getIncludesContentHashForHashes([]),
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf"])
        )

        # Wrong order
        self.assertNotEqual(
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf", "f6c8bd5733"]),
            ManifestRepository.getIncludesContentHashForHashes(["f6c8bd5733", "d88be7edbf"])
        )

        # Content in different elements
        self.assertNotEqual(
            ManifestRepository.getIncludesContentHashForHashes(["", "d88be7edbf"]),
            ManifestRepository.getIncludesContentHashForHashes(["d88be7edbf", ""])
        )
        self.assertNotEqual(
            ManifestRepository.getIncludesContentHashForHashes(["d88be", "7edbf"]),
            ManifestRepository.getIncludesContentHashForHashes(["d88b", "e7edbf"])
        )

    def testStoreAndGetManifest(self):
        with tempfile.TemporaryDirectory() as manifestsRootDir:
            mm = ManifestRepository(manifestsRootDir)

            ms1 = mm.section("8a33738d88be7edbacef48e262bbb5bc")
            ms2 = mm.section("0623305942d216c165970948424ae7d1")

            ms1.setManifest("8a33738d88be7edbacef48e262bbb5bc", TestManifestRepository.manifest1)
            ms2.setManifest("0623305942d216c165970948424ae7d1", TestManifestRepository.manifest2)

            retrieved1 = ms1.getManifest("8a33738d88be7edbacef48e262bbb5bc")
            self.assertIsNotNone(retrieved1)
            retrieved1Entry = retrieved1.entries()[0]
            self.assertEqual(retrieved1Entry, TestManifestRepository.entry1)

            retrieved2 = ms2.getManifest("0623305942d216c165970948424ae7d1")
            self.assertIsNotNone(retrieved2)
            retrieved2Entry = retrieved2.entries()[0]
            self.assertEqual(retrieved2Entry, TestManifestRepository.entry2)

    def testNonExistingManifest(self):
        manifestsRootDir = os.path.join(ASSETS_DIR, "manifests")
        mm = ManifestRepository(manifestsRootDir)

        retrieved = mm.section("ffffffffffffffffffffffffffffffff").getManifest("ffffffffffffffffffffffffffffffff")
        self.assertIsNone(retrieved)

    def testBrokenManifest(self):
        manifestsRootDir = os.path.join(ASSETS_DIR, "manifests")
        mm = ManifestRepository(manifestsRootDir)

        retrieved = mm.section("brokenmanifest").getManifest("brokenmanifest")
        self.assertIsNone(retrieved)

    def testClean(self):
        with tempfile.TemporaryDirectory() as manifestsRootDir:
            mm = ManifestRepository(manifestsRootDir)

            mm.section("8a33738d88be7edbacef48e262bbb5bc").setManifest("8a33738d88be7edbacef48e262bbb5bc",
                                                                       TestManifestRepository.manifest1)
            mm.section("0623305942d216c165970948424ae7d1").setManifest("0623305942d216c165970948424ae7d1",
                                                                       TestManifestRepository.manifest2)

            cleaningResultSize = mm.clean(240)
            # Only one of those manifests can be left
            self.assertLessEqual(cleaningResultSize, 240)
            self.assertLessEqual(self._getDirectorySize(manifestsRootDir), 240)

            cleaningResultSize = mm.clean(240)
            # The one remaining is remains alive
            self.assertLessEqual(cleaningResultSize, 240)
            self.assertGreaterEqual(cleaningResultSize, 120)
            self.assertLessEqual(self._getDirectorySize(manifestsRootDir), 240)
            self.assertGreaterEqual(self._getDirectorySize(manifestsRootDir), 120)

            cleaningResultSize = mm.clean(0)
            # All manifest are gone
            self.assertEqual(cleaningResultSize, 0)
            self.assertEqual(self._getDirectorySize(manifestsRootDir), 0)


class TestCompilerArtifactsRepository(unittest.TestCase):
    def testPaths(self):
        compilerArtifactsRepositoryRootDir = os.path.join(ASSETS_DIR, "compiler-artifacts-repository")
        car = CompilerArtifactsRepository(compilerArtifactsRepositoryRootDir)
        cas = car.section("fdde59862785f9f0ad6e661b9b5746b7")

        # section path
        self.assertEqual(cas.compilerArtifactsSectionDir, os.path.join(compilerArtifactsRepositoryRootDir, "fd"))

        # entry path
        self.assertEqual(cas.cachedObjectName("fdde59862785f9f0ad6e661b9b5746b7"), os.path.join(
            compilerArtifactsRepositoryRootDir, "fd", "fdde59862785f9f0ad6e661b9b5746b7", "object"))


class TestArgumentClasses(unittest.TestCase):
    def testEquality(self):
        self.assertEqual(clcache.ArgumentT1('Fo'), clcache.ArgumentT1('Fo'))
        self.assertEqual(clcache.ArgumentT1('W'), clcache.ArgumentT1('W'))
        self.assertEqual(clcache.ArgumentT2('W'), clcache.ArgumentT2('W'))
        self.assertEqual(clcache.ArgumentT3('W'), clcache.ArgumentT3('W'))
        self.assertEqual(clcache.ArgumentT4('W'), clcache.ArgumentT4('W'))

        self.assertNotEqual(clcache.ArgumentT1('Fo'), clcache.ArgumentT1('W'))
        self.assertNotEqual(clcache.ArgumentT1('Fo'), clcache.ArgumentT1('FO'))

        self.assertNotEqual(clcache.ArgumentT1('W'), clcache.ArgumentT2('W'))
        self.assertNotEqual(clcache.ArgumentT2('W'), clcache.ArgumentT3('W'))
        self.assertNotEqual(clcache.ArgumentT3('W'), clcache.ArgumentT4('W'))
        self.assertNotEqual(clcache.ArgumentT4('W'), clcache.ArgumentT1('W'))

    def testHash(self):
        self.assertEqual(hash(clcache.ArgumentT1('Fo')), hash(clcache.ArgumentT1('Fo')))
        self.assertEqual(hash(clcache.ArgumentT1('W')), hash(clcache.ArgumentT1('W')))
        self.assertEqual(hash(clcache.ArgumentT2('W')), hash(clcache.ArgumentT2('W')))
        self.assertEqual(hash(clcache.ArgumentT3('W')), hash(clcache.ArgumentT3('W')))
        self.assertEqual(hash(clcache.ArgumentT4('W')), hash(clcache.ArgumentT4('W')))

        self.assertNotEqual(hash(clcache.ArgumentT1('Fo')), hash(clcache.ArgumentT1('W')))
        self.assertNotEqual(hash(clcache.ArgumentT1('Fo')), hash(clcache.ArgumentT1('FO')))

        self.assertNotEqual(hash(clcache.ArgumentT1('W')), hash(clcache.ArgumentT2('W')))
        self.assertNotEqual(hash(clcache.ArgumentT2('W')), hash(clcache.ArgumentT3('W')))
        self.assertNotEqual(hash(clcache.ArgumentT3('W')), hash(clcache.ArgumentT4('W')))
        self.assertNotEqual(hash(clcache.ArgumentT4('W')), hash(clcache.ArgumentT1('W')))


class TestSplitCommandsFile(unittest.TestCase):
    def _genericTest(self, commandLine, expected):
        self.assertEqual(clcache.splitCommandsFile(commandLine), expected)

    def testEmpty(self):
        self._genericTest('', [])

    def testSimple(self):
        self._genericTest('/nologo', ['/nologo'])
        self._genericTest('/nologo /c', ['/nologo', '/c'])
        self._genericTest('/nologo /c -I.', ['/nologo', '/c', '-I.'])

    def testWhitespace(self):
        self._genericTest('-A -B    -C', ['-A', '-B', '-C'])
        self._genericTest('   -A -B -C', ['-A', '-B', '-C'])
        self._genericTest('-A -B -C   ', ['-A', '-B', '-C'])

    def testMicrosoftExamples(self):
        # https://msdn.microsoft.com/en-us/library/17w5ykft.aspx
        self._genericTest(r'"abc" d e', ['abc', 'd', 'e'])
        self._genericTest(r'a\\b d"e f"g h', [r'a\\b', 'de fg', 'h'])
        self._genericTest(r'a\\\"b c d', [r'a\"b', 'c', 'd'])
        self._genericTest(r'a\\\\"b c" d e', [r'a\\b c', 'd', 'e'])

    def testQuotesAroundArgument(self):
        self._genericTest(r'/Fo"C:\out dir\main.obj"', [r'/FoC:\out dir\main.obj'])
        self._genericTest(r'/c /Fo"C:\out dir\main.obj"', ['/c', r'/FoC:\out dir\main.obj'])
        self._genericTest(r'/Fo"C:\out dir\main.obj" /nologo', [r'/FoC:\out dir\main.obj', '/nologo'])
        self._genericTest(r'/c /Fo"C:\out dir\main.obj" /nologo', ['/c', r'/FoC:\out dir\main.obj', '/nologo'])

    def testDoubleQuoted(self):
        self._genericTest(r'"/Fo"something\main.obj""', [r'/Fosomething\main.obj'])
        self._genericTest(r'/c "/Fo"something\main.obj""', ['/c', r'/Fosomething\main.obj'])
        self._genericTest(r'"/Fo"something\main.obj"" /nologo', [r'/Fosomething\main.obj', '/nologo'])
        self._genericTest(r'/c "/Fo"something\main.obj"" /nologo', ['/c', r'/Fosomething\main.obj', '/nologo'])

    def testBackslashBeforeQuote(self):
        # Pathological cases of escaping the quote incorrectly.
        self._genericTest(r'/Fo"C:\out dir\"', [r'/FoC:\out dir"'])
        self._genericTest(r'/c /Fo"C:\out dir\"', ['/c', r'/FoC:\out dir"'])
        self._genericTest(r'/Fo"C:\out dir\" /nologo', [r'/FoC:\out dir" /nologo'])
        self._genericTest(r'/c /Fo"C:\out dir\" /nologo', ['/c', r'/FoC:\out dir" /nologo'])

        # Sane cases of escaping the backslash correctly.
        self._genericTest(r'/Fo"C:\out dir\\"', [r'/FoC:\out dir' '\\'])
        self._genericTest(r'/c /Fo"C:\out dir\\"', ['/c', r'/FoC:\out dir' '\\'])
        self._genericTest(r'/Fo"C:\out dir\\" /nologo', [r'/FoC:\out dir' '\\', r'/nologo'])
        self._genericTest(r'/c /Fo"C:\out dir\\" /nologo', ['/c', r'/FoC:\out dir' '\\', r'/nologo'])

    def testVyachselavCase(self):
        self._genericTest(
            r'"-IC:\Program files\Some library" -DX=1 -DVERSION=\"1.0\" -I..\.. -I"..\..\lib" -DMYPATH=\"C:\Path\"',
            [
                r'-IC:\Program files\Some library',
                r'-DX=1',
                r'-DVERSION="1.0"',
                r'-I..\..',
                r'-I..\..\lib',
                r'-DMYPATH="C:\Path"'
            ])

    def testLineEndings(self):
        self._genericTest('-A\n-B', ['-A', '-B'])
        self._genericTest('-A\r\n-B', ['-A', '-B'])
        self._genericTest('-A -B\r\n-C -D -E', ['-A', '-B', '-C', '-D', '-E'])

    def testInitialBackslash(self):
        self._genericTest(r'/Fo"C:\out dir\"', [r'/FoC:\out dir"'])
        self._genericTest(r'\foo.cpp', [r'\foo.cpp'])
        self._genericTest(r'/nologo \foo.cpp', [r'/nologo', r'\foo.cpp'])
        self._genericTest(r'\foo.cpp /c', [r'\foo.cpp', r'/c'])


class TestExpandCommandLine(unittest.TestCase):
    def _genericTest(self, commandLine, expected):
        with cd(os.path.join(ASSETS_DIR, "response-files")):
            self.assertEqual(clcache.expandCommandLine(commandLine), expected)

    def testNoResponseFile(self):
        self._genericTest(['-A', '-B'], ['-A', '-B'])

    def testMissingResponseFile(self):
        with self.assertRaises(FileNotFoundError):
            self._genericTest(['-A', '@no_such_file.rsp', '-B'], [])

    def testSingleResponseFile(self):
        self._genericTest(['-A', '@default_encoded.rsp', '-B'], ['-A', '/DPASSWORD=Käse', '/nologo', '-B'])

    def testMultipleResponseFile(self):
        self._genericTest(
            ['-A', '@default_encoded.rsp', '@utf16_encoded.rsp', '-B'],
            ['-A', '/DPASSWORD=Käse', '/nologo', '/DPASSWORD=Фёдор', '/IC:\\Users\\Миха́йлович', '-B']
        )

    def testNestedResponseFiles(self):
        self._genericTest(
            ['-A', '@nested_response_file.rsp', '-B'],
            ['-A', '/O2', '/DSOMETHING=foo', '/DANOTHERTHING=bar', '/nologo', '-B']
        )


class TestAnalyzeCommandLine(unittest.TestCase):
    def _testSourceFilesOk(self, cmdLine):
        try:
            CommandLineAnalyzer.analyze(cmdLine)
        except AnalysisError as err:
            if isinstance(err, NoSourceFileError):
                self.fail("analyze() unexpectedly raised an NoSourceFileError")
            else:
                # We just want to know if we got a proper source file.
                # Other AnalysisErrors are ignored.
                pass

    def _testFailure(self, cmdLine, expectedExceptionClass):
        self.assertRaises(expectedExceptionClass, lambda: CommandLineAnalyzer.analyze(cmdLine))

    def _testFull(self, cmdLine, expectedSourceFiles, expectedOutputFile):
        sourceFiles, outputFile = CommandLineAnalyzer.analyze(cmdLine)
        self.assertEqual(sourceFiles, expectedSourceFiles)
        self.assertEqual(outputFile, expectedOutputFile)

    def _testFo(self, foArgument, expectedObjectFilepath):
        self._testFull(['/c', foArgument, 'main.cpp'],
                       ["main.cpp"], [expectedObjectFilepath])

    def _testFi(self, fiArgument):
        self._testPreprocessingOutfile(['/c', '/P', fiArgument, 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/EP', fiArgument, 'main.cpp'])

    def _testPreprocessingOutfile(self, cmdLine):
        self._testFailure(cmdLine, CalledForPreprocessingError)

    def _testArgInfiles(self, cmdLine, expectedArguments, expectedInputFiles):
        arguments, inputFiles = CommandLineAnalyzer.parseArgumentsAndInputFiles(cmdLine)
        self.assertEqual(arguments, expectedArguments)
        self.assertEqual(inputFiles, expectedInputFiles)

    def testEmpty(self):
        self._testFailure([], NoSourceFileError)

    def testSimple(self):
        self._testFull(["/c", "main.cpp"], ["main.cpp"], ["main.obj"])

    def testNoSource(self):
        # No source file has priority over other errors, for consistency
        # and because it's likely to be a misconfigured command line.
        self._testFailure(['/c', '/nologo'], NoSourceFileError)
        self._testFailure(['/c'], NoSourceFileError)
        self._testFailure([], NoSourceFileError)
        self._testFailure(['/Zi'], NoSourceFileError)
        self._testFailure(['/E'], NoSourceFileError)
        self._testFailure(['/P'], NoSourceFileError)
        self._testFailure(['/EP'], NoSourceFileError)
        self._testFailure(['/Yc'], NoSourceFileError)
        self._testFailure(['/Yu'], NoSourceFileError)
        self._testFailure(['/link'], NoSourceFileError)

    def testOutputFileFromSourcefile(self):
        # For object file
        self._testFull(['/c', 'main.cpp'],
                       ['main.cpp'], ['main.obj'])
        # For preprocessor file
        self._testFailure(['/c', '/P', 'main.cpp'], CalledForPreprocessingError)

    def testPreprocessIgnoresOtherArguments(self):
        # All those inputs must ignore the /Fo, /Fa and /Fm argument according
        # to the documentation of /E, /P and /EP

        # to file (/P)
        self._testPreprocessingOutfile(['/c', '/P', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/FoSome.obj', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/FaListing.asm', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/FmMapfile.map', 'main.cpp'])

        # to file (/P /EP)
        # Note: documentation bug in https://msdn.microsoft.com/en-us/library/becb7sys.aspx
        self._testPreprocessingOutfile(['/c', '/P', '/EP', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/EP', '/FoSome.obj', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/EP', '/FaListing.asm', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/P', '/EP', '/FmMapfile.map', 'main.cpp'])

        # to stdout (/E)
        self._testPreprocessingOutfile(['/c', '/E', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/E', '/FoSome.obj', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/E', '/FaListing.asm', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/E', '/FmMapfile.map', 'main.cpp'])

        # to stdout (/EP)
        self._testPreprocessingOutfile(['/c', '/EP', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/EP', '/FoSome.obj', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/EP', '/FaListing.asm', 'main.cpp'])
        self._testPreprocessingOutfile(['/c', '/EP', '/FmMapfile.map', 'main.cpp'])

    def testOutputFile(self):
        # Given object filename (default extension .obj)
        self._testFo('/FoTheOutFile.obj', 'TheOutFile.obj')

        # Given object filename (custom extension .dat)
        self._testFo('/FoTheOutFile.dat', 'TheOutFile.dat')

        # Given object filename (with spaces)
        self._testFo('/FoThe Out File.obj', 'The Out File.obj')

        # Existing directory
        with cd(ASSETS_DIR):
            self._testFo(r'/Fo.', r'.\main.obj')
            self._testFo(r'/Fofo-build-debug', r'fo-build-debug\main.obj')
            self._testFo(r'/Fofo-build-debug\\', r'fo-build-debug\main.obj')

    def testOutputFileNormalizePath(self):
        # Out dir does not exist, but preserve path. Compiler will complain
        self._testFo(r'/FoDebug\TheOutFile.obj', r'Debug\TheOutFile.obj')

        # Convert to Windows path separatores (like cl does too)
        self._testFo(r'/FoDebug/TheOutFile.obj', r'Debug\TheOutFile.obj')

        # Different separators work as well
        self._testFo(r'/FoDe\bug/TheOutFile.obj', r'De\bug\TheOutFile.obj')

        # Double slash
        self._testFo(r'/FoDebug//TheOutFile.obj', r'Debug\TheOutFile.obj')
        self._testFo(r'/FoDebug\\TheOutFile.obj', r'Debug\TheOutFile.obj')

    def testPreprocessingFi(self):
        # Given output filename
        self._testFi('/FiTheOutFile.i')
        self._testFi('/FiTheOutFile.dat')
        self._testFi('/FiThe Out File.i')

        # Existing directory
        with cd(ASSETS_DIR):
            self._testFi(r'/Fi.')
            self._testFi(r'/Fifi-build-debug')
            self._testFi(r'/Fifi-build-debug\\')

        # Non-existing directory: preserve path, compiler will complain
        self._testFi(r'/FiDebug\TheOutFile.i')

        # Convert to single Windows path separatores (like cl does too)
        self._testFi(r'/FiDebug/TheOutFile.i')
        self._testFi(r'/FiDe\bug/TheOutFile.i')
        self._testFi(r'/FiDebug//TheOutFile.i')
        self._testFi(r'/FiDebug\\TheOutFile.i')

    def testTpTcSimple(self):
        # clcache can handle /Tc or /Tp as long as there is only one of them
        self._testFull(['/c', '/TcMyCcProgram.c'],
                       ['MyCcProgram.c'], ['MyCcProgram.obj'])
        self._testFull(['/c', '/TpMyCxxProgram.cpp'],
                       ['MyCxxProgram.cpp'], ['MyCxxProgram.obj'])

    def testLink(self):
        self._testFailure(["main.cpp"], CalledForLinkError)
        self._testFailure(["/nologo", "main.cpp"], CalledForLinkError)

    def testArgumentParameters(self):
        # Type 1 (/NAMEparameter) - Arguments with required parameter
        self._testFailure(["/c", "/Ob", "main.cpp"], InvalidArgumentError)
        self._testFailure(["/c", "/Yl", "main.cpp"], InvalidArgumentError)
        self._testFailure(["/c", "/Zm", "main.cpp"], InvalidArgumentError)
        self._testSourceFilesOk(["/c", "/Ob999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Yl999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Zm999", "main.cpp"])

        # Type 2 (/NAME[parameter]) - Optional argument parameters must not eat up source file
        self._testSourceFilesOk(["/c", "/doc", "main.cpp"])
        self._testSourceFilesOk(["/c", "/FA", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fr", "main.cpp"])
        self._testSourceFilesOk(["/c", "/FR", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Gs", "main.cpp"])
        self._testSourceFilesOk(["/c", "/MP", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Wv", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Yc", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Yu", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Zp", "main.cpp"])

        # Type 3 (/NAME[ ]parameter) - Required argument parameters with optional space eat up source file
        self._testFailure(["/c", "/FI", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/U", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/I", "main.cpp"], NoSourceFileError)
        self._testSourceFilesOk(["/c", "/FI9999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/U9999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/I9999", "main.cpp"])

        # Type 4 (/NAME parameter) - Forced space
        # Some documented, but non implemented

        # Documented as type 1 (/NAMEparmeter) but work as type 2 (/NAME[parameter])
        self._testSourceFilesOk(["/c", "/Fa", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fi", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fd", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fe", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fm", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fo", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Fp", "main.cpp"])

        # Documented as type 1 (/NAMEparmeter) but work as type 3 (/NAME[ ]parameter)
        self._testFailure(["/c", "/AI", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/D", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/V", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/w1", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/w2", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/w3", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/w4", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/wd", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/we", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/wo", "main.cpp"], NoSourceFileError)
        self._testSourceFilesOk(["/c", "/AI999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/D999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/V999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/w1999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/w2999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/w3999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/w4999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/wd999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/we999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/wo999", "main.cpp"])
        # Those work a bit differently
        self._testSourceFilesOk(["/c", "/Tc", "main.cpp"])
        self._testSourceFilesOk(["/c", "/Tp", "main.cpp"])
        self._testFailure(["/c", "/Tc", "999", "main.cpp"], MultipleSourceFilesComplexError)
        self._testFailure(["/c", "/Tp", "999", "main.cpp"], MultipleSourceFilesComplexError)
        self._testFailure(["/c", "/Tc999", "main.cpp"], MultipleSourceFilesComplexError)
        self._testFailure(["/c", "/Tp999", "main.cpp"], MultipleSourceFilesComplexError)

        # Documented as type 4 (/NAME parameter) but work as type 3 (/NAME[ ]parameter)
        self._testFailure(["/c", "/F", "main.cpp"], NoSourceFileError)
        self._testFailure(["/c", "/FU", "main.cpp"], NoSourceFileError)
        self._testSourceFilesOk(["/c", "/F999", "main.cpp"])
        self._testSourceFilesOk(["/c", "/FU999", "main.cpp"])

    def testParseArgumentsAndInputFiles(self):
        self._testArgInfiles(['/c', 'main.cpp'],
                             {'c': ['']},
                             ['main.cpp'])
        self._testArgInfiles(['/link', 'unit1.obj', 'unit2.obj'],
                             {'link': ['']},
                             ['unit1.obj', 'unit2.obj'])
        self._testArgInfiles(['/Fooutfile.obj', 'main.cpp'],
                             {'Fo': ['outfile.obj']},
                             ['main.cpp'])
        self._testArgInfiles(['/Fo', '/Fooutfile.obj', 'main.cpp'],
                             {'Fo': ['', 'outfile.obj']},
                             ['main.cpp'])
        self._testArgInfiles(['/c', '/I', 'somedir', 'main.cpp'],
                             {'c': [''], 'I': ['somedir']},
                             ['main.cpp'])
        self._testArgInfiles(['/c', '/I.', '/I', 'somedir', 'main.cpp'],
                             {'c': [''], 'I': ['.', 'somedir']},
                             ['main.cpp'])

        # Type 1 (/NAMEparameter) - Arguments with required parameter
        # get parameter=99
        self._testArgInfiles(["/c", "/Ob99", "main.cpp"], {'c': [''], 'Ob': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yl99", "main.cpp"], {'c': [''], 'Yl': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Zm99", "main.cpp"], {'c': [''], 'Zm': ['99']}, ['main.cpp'])

        # # Type 2 (/NAME[parameter]) - Optional argument parameters
        # get parameter=99
        self._testArgInfiles(["/c", "/doc99", "main.cpp"], {'c': [''], 'doc': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FA99", "main.cpp"], {'c': [''], 'FA': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fr99", "main.cpp"], {'c': [''], 'Fr': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FR99", "main.cpp"], {'c': [''], 'FR': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Gs99", "main.cpp"], {'c': [''], 'Gs': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/MP99", "main.cpp"], {'c': [''], 'MP': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Wv99", "main.cpp"], {'c': [''], 'Wv': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yc99", "main.cpp"], {'c': [''], 'Yc': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yu99", "main.cpp"], {'c': [''], 'Yu': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Zp99", "main.cpp"], {'c': [''], 'Zp': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fa99", "main.cpp"], {'c': [''], 'Fa': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fd99", "main.cpp"], {'c': [''], 'Fd': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fe99", "main.cpp"], {'c': [''], 'Fe': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fi99", "main.cpp"], {'c': [''], 'Fi': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fm99", "main.cpp"], {'c': [''], 'Fm': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fo99", "main.cpp"], {'c': [''], 'Fo': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fp99", "main.cpp"], {'c': [''], 'Fp': ['99']}, ['main.cpp'])
        # get no parameter
        self._testArgInfiles(["/c", "/doc", "main.cpp"], {'c': [''], 'doc': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FA", "main.cpp"], {'c': [''], 'FA': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fr", "main.cpp"], {'c': [''], 'Fr': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FR", "main.cpp"], {'c': [''], 'FR': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Gs", "main.cpp"], {'c': [''], 'Gs': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/MP", "main.cpp"], {'c': [''], 'MP': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Wv", "main.cpp"], {'c': [''], 'Wv': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yc", "main.cpp"], {'c': [''], 'Yc': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Yu", "main.cpp"], {'c': [''], 'Yu': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Zp", "main.cpp"], {'c': [''], 'Zp': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fa", "main.cpp"], {'c': [''], 'Fa': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fd", "main.cpp"], {'c': [''], 'Fd': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fe", "main.cpp"], {'c': [''], 'Fe': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fi", "main.cpp"], {'c': [''], 'Fi': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fm", "main.cpp"], {'c': [''], 'Fm': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fo", "main.cpp"], {'c': [''], 'Fo': ['']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Fp", "main.cpp"], {'c': [''], 'Fp': ['']}, ['main.cpp'])

        # Type 3 (/NAME[ ]parameter) - Required argument parameters with optional space
        # get space
        self._testArgInfiles(["/c", "/FI", "99", "main.cpp"], {'c': [''], 'FI': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/U", "99", "main.cpp"], {'c': [''], 'U': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/I", "99", "main.cpp"], {'c': [''], 'I': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/F", "99", "main.cpp"], {'c': [''], 'F': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FU", "99", "main.cpp"], {'c': [''], 'FU': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w1", "99", "main.cpp"], {'c': [''], 'w1': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w2", "99", "main.cpp"], {'c': [''], 'w2': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w3", "99", "main.cpp"], {'c': [''], 'w3': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w4", "99", "main.cpp"], {'c': [''], 'w4': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/wd", "99", "main.cpp"], {'c': [''], 'wd': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/we", "99", "main.cpp"], {'c': [''], 'we': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/wo", "99", "main.cpp"], {'c': [''], 'wo': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/AI", "99", "main.cpp"], {'c': [''], 'AI': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/D", "99", "main.cpp"], {'c': [''], 'D': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/V", "99", "main.cpp"], {'c': [''], 'V': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Tc", "99", "main.cpp"], {'c': [''], 'Tc': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Tp", "99", "main.cpp"], {'c': [''], 'Tp': ['99']}, ['main.cpp'])
        # don't get space
        self._testArgInfiles(["/c", "/FI99", "main.cpp"], {'c': [''], 'FI': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/U99", "main.cpp"], {'c': [''], 'U': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/I99", "main.cpp"], {'c': [''], 'I': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/F99", "main.cpp"], {'c': [''], 'F': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/FU99", "main.cpp"], {'c': [''], 'FU': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w199", "main.cpp"], {'c': [''], 'w1': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w299", "main.cpp"], {'c': [''], 'w2': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w399", "main.cpp"], {'c': [''], 'w3': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/w499", "main.cpp"], {'c': [''], 'w4': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/wd99", "main.cpp"], {'c': [''], 'wd': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/we99", "main.cpp"], {'c': [''], 'we': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/wo99", "main.cpp"], {'c': [''], 'wo': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/AI99", "main.cpp"], {'c': [''], 'AI': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/D99", "main.cpp"], {'c': [''], 'D': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/V99", "main.cpp"], {'c': [''], 'V': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Tc99", "main.cpp"], {'c': [''], 'Tc': ['99']}, ['main.cpp'])
        self._testArgInfiles(["/c", "/Tp99", "main.cpp"], {'c': [''], 'Tp': ['99']}, ['main.cpp'])

        # Type 4 (/NAME parameter) - Forced space
        # Some documented, but non implemented


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


class TestParseIncludes(unittest.TestCase):
    def _readSampleFileDefault(self, lang=None):
        if lang == "de":
            filePath = os.path.join(ASSETS_DIR, 'parse-includes', 'compiler_output_lang_de.txt')
            uniqueIncludesCount = 82
        else:
            filePath = os.path.join(ASSETS_DIR, 'parse-includes', 'compiler_output.txt')
            uniqueIncludesCount = 83

        with open(filePath, 'r') as infile:
            return {
                'CompilerOutput': infile.read(),
                'UniqueIncludesCount': uniqueIncludesCount
            }

    def _readSampleFileNoIncludes(self):
        with open(os.path.join(ASSETS_DIR, 'parse-includes', 'compiler_output_no_includes.txt'), 'r') as infile:
            return {
                'CompilerOutput': infile.read(),
                'UniqueIncludesCount': 0
            }

    def testParseIncludesNoStrip(self):
        sample = self._readSampleFileDefault()
        includesSet, newCompilerOutput = clcache.parseIncludesSet(
            sample['CompilerOutput'],
            r'C:\Projects\test\smartsqlite\src\version.cpp',
            strip=False)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\projects\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(
            r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)
        self.assertEqual(newCompilerOutput, sample['CompilerOutput'])

    def testParseIncludesStrip(self):
        sample = self._readSampleFileDefault()
        includesSet, newCompilerOutput = clcache.parseIncludesSet(
            sample['CompilerOutput'],
            r'C:\Projects\test\smartsqlite\src\version.cpp',
            strip=True)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\projects\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(
            r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)
        self.assertEqual(newCompilerOutput, "version.cpp\n")

    def testParseIncludesNoIncludes(self):
        sample = self._readSampleFileNoIncludes()
        for stripIncludes in [True, False]:
            includesSet, newCompilerOutput = clcache.parseIncludesSet(
                sample['CompilerOutput'],
                r"C:\Projects\test\myproject\main.cpp",
                strip=stripIncludes)

            self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
            self.assertEqual(newCompilerOutput, "main.cpp\n")

    def testParseIncludesGerman(self):
        sample = self._readSampleFileDefault(lang="de")
        includesSet, _ = clcache.parseIncludesSet(
            sample['CompilerOutput'],
            r"C:\Projects\test\smartsqlite\src\version.cpp",
            strip=False)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\projects\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(
            r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)


class TestManifest(unittest.TestCase):
    entry1 = ManifestEntry([r'somepath\myinclude.h'],
                           "fdde59862785f9f0ad6e661b9b5746b7",
                           "a649723940dc975ebd17167d29a532f8")
    entry2 = ManifestEntry([r'somepath\myinclude.h', r'moreincludes.h'],
                           "474e7fc26a592d84dfa7416c10f036c6",
                           "8771d7ebcf6c8bd57a3d6485f63e3a89")
    entries = [entry1, entry2]

    def testCreateEmpty(self):
        manifest = Manifest()
        self.assertFalse(manifest.entries())

    def testCreateWithEntries(self):
        manifest = Manifest(TestManifest.entries)
        self.assertEqual(TestManifest.entries, manifest.entries())


    def testAddEntry(self):
        manifest = Manifest(TestManifest.entries)
        newEntry = ManifestEntry([r'somepath\myotherinclude.h'],
                                 "474e7fc26a592d84dfa7416c10f036c6",
                                 "8771d7ebcf6c8bd57a3d6485f63e3a89")
        manifest.addEntry(newEntry)
        self.assertEqual(newEntry, manifest.entries()[0])


    def testTouchEntry(self):
        manifest = Manifest(TestManifest.entries)
        self.assertEqual(TestManifest.entry1, manifest.entries()[0])
        manifest.touchEntry(1)
        self.assertEqual(TestManifest.entry2, manifest.entries()[0])


class TestCreateManifestEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempDir = tempfile.TemporaryDirectory()
        for i in range(10):
            sampleName = 'sample{}.h'.format(i)
            filePath = os.path.join(cls.tempDir.name, '{}.h'.format(sampleName))
            with open(filePath, 'w') as f:
                f.write('#define {}'.format(sampleName))

        cls.includePaths = list(sorted(clcache.filesBeneath(cls.tempDir.name)))
        cls.manifestHash = 'ffffffffffffffffffffffffffffffff'
        cls.expectedManifestEntry = clcache.createManifestEntry(TestCreateManifestEntry.manifestHash,
                                                                TestCreateManifestEntry.includePaths)

    @classmethod
    def tearDownClass(cls):
        cls.tempDir.cleanup()

    def assertManifestEntryIsCorrect(self, entry):
        self.assertEqual(entry.includesContentHash, TestCreateManifestEntry.expectedManifestEntry.includesContentHash)
        self.assertEqual(entry.objectHash, TestCreateManifestEntry.expectedManifestEntry.objectHash)
        self.assertEqual(entry.includeFiles, TestCreateManifestEntry.expectedManifestEntry.includeFiles)

    def testIsConsistentWithSameInput(self):
        entry = clcache.createManifestEntry(TestCreateManifestEntry.manifestHash, TestCreateManifestEntry.includePaths)
        self.assertManifestEntryIsCorrect(entry)

    def testIsConsistentWithReverseList(self):
        reversedIncludePaths = list(reversed(TestCreateManifestEntry.includePaths))
        entry = clcache.createManifestEntry(TestCreateManifestEntry.manifestHash, reversedIncludePaths)
        self.assertManifestEntryIsCorrect(entry)

    def testIsConsistentWithDuplicateEntries(self):
        includePathsWithDuplicates = TestCreateManifestEntry.includePaths + TestCreateManifestEntry.includePaths
        entry = clcache.createManifestEntry(TestCreateManifestEntry.manifestHash, includePathsWithDuplicates)
        self.assertManifestEntryIsCorrect(entry)


class TestPersistentJSONDict(unittest.TestCase):
    def testEmptyFile(self):
        emptyFile = os.path.join(ASSETS_DIR, "empty_file.txt")
        PersistentJSONDict(emptyFile)

    def testBrokenJson(self):
        brokenJson = os.path.join(ASSETS_DIR, "broken_json.txt")
        PersistentJSONDict(brokenJson)


class TestMemcacheStrategy(unittest.TestCase):
    def testSetGet(self):
        from pymemcache.test.utils import MockMemcacheClient
        from clcache import CompilerArtifacts, getStringHash

        with tempfile.TemporaryDirectory() as tempDir:
            memcache = CacheMemcacheStrategy("localhost", cacheDirectory=tempDir)
            memcache.client = MockMemcacheClient(allow_unicode_keys=True)
            key = getStringHash("hello")
            memcache.fileStrategy.lockFor = memcache.lockFor

            self.assertEqual(memcache.hasEntry(key), False)
            self.assertEqual(memcache.getEntry(key), None)

            dirName = memcache.fileStrategy.directoryForCache(key)  # XX requires in depth knowledge of FileStrategy
            os.makedirs(dirName)
            fileName = os.path.join(dirName, "object")
            with open(fileName, "wb") as f:
                f.write(b'Content')

            artifact = CompilerArtifacts(fileName, "", "")

            memcache.setEntry(key, artifact)
            self.assertEqual(memcache.hasEntry(key), True)
            self.assertEqual(memcache.getEntry(key).objectFilePath, artifact.objectFilePath)
            self.assertEqual(memcache.getEntry(key).stdout, artifact.stdout)
            self.assertEqual(memcache.getEntry(key).stderr, artifact.stderr)

            nonArtifact = CompilerArtifacts("random.txt", "stdout", "stderr")
            with self.assertRaises(FileNotFoundError):
                memcache.setEntry(key, nonArtifact)

    def testArgumentParsing(self):
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("")

        self.assertEqual(CacheMemcacheStrategy.splitHosts("localhost"), [("localhost", 11211)])
        self.assertEqual(CacheMemcacheStrategy.splitHosts("localhost:123"), [("localhost", 123)])

        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost:123:")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost:123:")

        self.assertEqual(CacheMemcacheStrategy.splitHosts("localhost.local, example.local"),
                         [("localhost.local", 11211), ("example.local", 11211)])
        self.assertEqual(CacheMemcacheStrategy.splitHosts("localhost.local:12345, example.local"),
                         [("localhost.local", 12345), ("example.local", 11211)])

        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local:123456")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local:123456, example.local")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local:12345,")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local,12345:")
        with self.assertRaises(ValueError):
            CacheMemcacheStrategy.splitHosts("localhost.local;12345:")


if __name__ == '__main__':
    unittest.TestCase.longMessage = True
    unittest.main()
