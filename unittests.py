#!/usr/bin/env python
#
# This file is part of the clcache project.
#
# Copyright (c)
#   2016 Simon Warta (Kullo GmbH)
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# In Python unittests are always members, not functions. Silence lint in this file.
# pylint: disable=no-self-use
#
from __future__ import print_function
from __future__ import unicode_literals # All string literals are unicode strings. Requires Python 3.3+
from __future__ import division

from contextlib import contextmanager
import multiprocessing
import os
import unittest

import clcache
from clcache import CommandLineAnalyzer
from clcache import CalledForPreprocessingError, NoSourceFileError, CalledForLinkError


ASSETS_DIR = os.path.join("tests", "unittests")


@contextmanager
def cd(targetDirectory):
    oldDirectory = os.getcwd()
    os.chdir(os.path.expanduser(targetDirectory))
    try:
        yield
    finally:
        os.chdir(oldDirectory)


class BaseTest(unittest.TestCase):
    def __init__(self, *args, **kwargs):
        self.longMessage = True
        super(BaseTest, self).__init__(*args, **kwargs)


class TestHelperFunctions(BaseTest):
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


class TestSplitCommandsFile(BaseTest):
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


class TestAnalyzeCommandLine(BaseTest):
    def _testFailure(self, cmdLine, expectedExceptionClass):
        self.assertRaises(expectedExceptionClass, lambda: CommandLineAnalyzer.analyze(cmdLine))

    def _testFull(self, cmdLine, expectedSourceFiles, expectedOutputFile):
        sourceFiles, outputFile = CommandLineAnalyzer.analyze(cmdLine)
        self.assertEqual(sourceFiles, expectedSourceFiles)
        self.assertEqual(outputFile, expectedOutputFile)

    def _testFo(self, foArgument, expectedObjectFilepath):
        self._testFull(['/c', foArgument, 'main.cpp'],
                       ["main.cpp"], expectedObjectFilepath)

    def _testFi(self, fiArgument):
        self._testFailure(['/c', '/P', fiArgument, 'main.cpp'], CalledForPreprocessingError)
        self._testFailure(['/c', '/P', '/EP', fiArgument, 'main.cpp'], CalledForPreprocessingError)

    def _testPreprocessingOutfile(self, cmdLine):
        self._testFailure(cmdLine, CalledForPreprocessingError)

    def testEmpty(self):
        self._testFailure([], NoSourceFileError)

    def testSimple(self):
        self._testFull(["/c", "main.cpp"], ["main.cpp"], "main.obj")

    def testNoSource(self):
        self._testFailure(['/c'], NoSourceFileError)
        self._testFailure(['/c', '/nologo'], NoSourceFileError)
        self._testFailure(['/c', '/nologo', '/Zi'], NoSourceFileError)

    def testOutputFileFromSourcefile(self):
        # For object file
        self._testFull(['/c', 'main.cpp'],
                       ['main.cpp'], 'main.obj')
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
                       ['MyCcProgram.c'], 'MyCcProgram.obj')
        self._testFull(['/c', '/TpMyCxxProgram.cpp'],
                       ['MyCxxProgram.cpp'], 'MyCxxProgram.obj')

    def testLink(self):
        self._testFailure(["main.cpp"], CalledForLinkError)
        self._testFailure(["/nologo", "main.cpp"], CalledForLinkError)


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


class TestParseIncludes(BaseTest):
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
        includesSet, newCompilerOutput = clcache.parseIncludesList(
            sample['CompilerOutput'],
            r'C:\Projects\test\smartsqlite\src\version.cpp',
            None,
            strip=False)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\projects\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(
            r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)
        self.assertEqual(newCompilerOutput, sample['CompilerOutput'])

    def testParseIncludesStrip(self):
        sample = self._readSampleFileDefault()
        includesSet, newCompilerOutput = clcache.parseIncludesList(
            sample['CompilerOutput'],
            r'C:\Projects\test\smartsqlite\src\version.cpp',
            None,
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
            includesSet, newCompilerOutput = clcache.parseIncludesList(
                sample['CompilerOutput'],
                r"C:\Projects\test\myproject\main.cpp",
                None,
                strip=stripIncludes)

            self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
            self.assertEqual(newCompilerOutput, "main.cpp\n")

    def testParseIncludesGerman(self):
        sample = self._readSampleFileDefault(lang="de")
        includesSet, _ = clcache.parseIncludesList(
            sample['CompilerOutput'],
            r"C:\Projects\test\smartsqlite\src\version.cpp",
            None,
            strip=False)

        self.assertEqual(len(includesSet), sample['UniqueIncludesCount'])
        self.assertTrue(r'c:\projects\test\smartsqlite\include\smartsqlite\version.h' in includesSet)
        self.assertTrue(
            r'c:\program files (x86)\microsoft visual studio 12.0\vc\include\concurrencysal.h' in includesSet)
        self.assertTrue(r'' not in includesSet)


if __name__ == '__main__':
    unittest.main()
