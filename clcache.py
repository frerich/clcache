#!/usr/bin/env python
#
# This file is part of the clcache project.
#
# Copyright (c)
#   2010, 2011, 2012, 2013, 2016 froglogic GmbH <raabe@froglogic.com>
#   2016 Simon Warta (Kullo GmbH)
#   2016 Tim Blechmann
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
from __future__ import print_function
from __future__ import unicode_literals # All string literals are unicode strings. Requires Python 3.3+
from __future__ import division

from ctypes import windll, wintypes
import codecs
from collections import defaultdict, namedtuple
try:
    import cPickle as pickle
except ImportError:
    import pickle
import errno
import hashlib
import json
import os
from shutil import copyfile, rmtree
import subprocess
from subprocess import Popen, PIPE
import sys
import multiprocessing
import re

VERSION = "3.1.2-dev"

HashAlgorithm = hashlib.md5


# The codec that is used by clcache to store compiler STDOUR and STDERR in
# output.txt and stderr.txt.
# This codec is up to us and only used for clcache internal storage.
# For possible values see https://docs.python.org/2/library/codecs.html
CACHE_COMPILER_OUTPUT_STORAGE_CODEC = 'utf-8'

# The cl default codec
CL_DEFAULT_CODEC = 'mbcs'

# Manifest file will have at most this number of hash lists in it. Need to avoi
# manifests grow too large.
MAX_MANIFEST_HASHES = 100

# String, by which BASE_DIR will be replaced in paths, stored in manifests.
# ? is invalid character for file name, so it seems ok
# to use it as mark for relative path.
BASEDIR_REPLACEMENT = '?'

# includeFiles - list of paths toi include files, which this source file use.
# hashes - dictionary.
# Key - cumulative hash of all include files in includeFiles;
# Value - key in the cache, under which output file is stored.
Manifest = namedtuple('Manifest', ['includeFiles', 'hashes'])


def printBinary(rawData, stream):
    with os.fdopen(stream.fileno(), 'wb') as fp:
        fp.write(rawData)


def basenameWithoutExtension(path):
    basename = os.path.basename(path)
    return os.path.splitext(basename)[0]


class ObjectCacheLockException(Exception):
    pass


class LogicException(Exception):
    def __init__(self, message):
        super(LogicException, self).__init__(message)
        self.message = message

    def __str__(self):
        return repr(self.message)


class ObjectCacheLock(object):
    """ Implements a lock for the object cache which
    can be used in 'with' statements. """
    INFINITE = 0xFFFFFFFF
    WAIT_ABANDONED_CODE = 0x00000080

    def __init__(self, mutexName, timeoutMs):
        mutexName = 'Local\\' + mutexName
        self._mutex = windll.kernel32.CreateMutexW(
            wintypes.INT(0),
            wintypes.INT(0),
            mutexName)
        self._timeoutMs = timeoutMs
        self._acquired = False
        assert self._mutex

    def __enter__(self):
        if not self._acquired:
            self.acquire()

    def __exit__(self, typ, value, traceback):
        if self._acquired:
            self.release()

    def __del__(self):
        windll.kernel32.CloseHandle(self._mutex)

    def acquire(self):
        result = windll.kernel32.WaitForSingleObject(
            self._mutex, wintypes.INT(self._timeoutMs))
        if result != 0 and result != self.WAIT_ABANDONED_CODE:
            errorString = 'Error! WaitForSingleObject returns {result}, last error {error}'.format(
                result=result,
                error=windll.kernel32.GetLastError())
            raise ObjectCacheLockException(errorString)
        self._acquired = True

    def release(self):
        windll.kernel32.ReleaseMutex(self._mutex)
        self._acquired = False


class ObjectCache(object):
    def __init__(self):
        try:
            self.dir = os.environ["CLCACHE_DIR"]
        except KeyError:
            self.dir = os.path.join(os.path.expanduser("~"), "clcache")
        self.manifestsDir = os.path.join(self.dir, "manifests")
        ensureDirectoryExists(self.manifestsDir)
        self.objectsDir = os.path.join(self.dir, "objects")
        ensureDirectoryExists(self.objectsDir)
        lockName = self.cacheDirectory().replace(':', '-').replace('\\', '-')
        timeoutMs = int(os.environ.get('CLCACHE_OBJECT_CACHE_TIMEOUT_MS', 10 * 1000))
        self.lock = ObjectCacheLock(lockName, timeoutMs)

    def cacheDirectory(self):
        return self.dir

    def clean(self, stats, maximumSize):
        currentSize = stats.currentCacheSize()
        if currentSize < maximumSize:
            return

        # Free at least 10% to avoid cleaning up too often which
        # is a big performance hit with large caches.
        effectiveMaximumSize = maximumSize * 0.9

        # try to use os.scandir or scandir.scandir
        # fall back to os.walk if not found
        try:
            import scandir
            walker = scandir.walk
        except ImportError:
            walker = os.walk

        objects = [os.path.join(root, "object")
                   for root, _, files in walker(self.objectsDir)
                   if "object" in files]

        objectInfos = []
        for o in objects:
            try:
                objectInfos.append((os.stat(o), o))
            except WindowsError:
                pass

        objectInfos.sort(key=lambda t: t[0].st_atime)

        # compute real current size to fix up the stored cacheSize
        currentSize = sum(x[0].st_size for x in objectInfos)

        removedItems = 0
        for stat, fn in objectInfos:
            rmtree(os.path.split(fn)[0], ignore_errors=True)
            removedItems += 1
            currentSize -= stat.st_size
            if currentSize < effectiveMaximumSize:
                break

        stats.setCacheSize(currentSize)

        stats.setNumCacheEntries(len(objectInfos) - removedItems)


    def removeObjects(self, stats, removedObjects):
        for o in removedObjects:
            dirPath = self._cacheEntryDir(o)
            if not os.path.exists(dirPath):
                continue  # May be if object already evicted.
            objectPath = os.path.join(dirPath, "object")
            if os.path.exists(objectPath):
                # May be absent if this if cached compiler
                # output (for preprocess-only).
                fileStat = os.stat(objectPath)
                stats.unregisterCacheEntry(fileStat.st_size)
            rmtree(dirPath, ignore_errors=True)


    @staticmethod
    def getManifestHash(compilerBinary, commandLine, sourceFile):
        compilerHash = getCompilerHash(compilerBinary)

        # NOTE: We intentionally do not normalize command line to include
        # preprocessor options. In direct mode we do not perform
        # preprocessing before cache lookup, so all parameters are important
        additionalData = compilerHash + ' '.join(commandLine)
        return getFileHash(sourceFile, additionalData)

    @staticmethod
    def computeKey(compilerBinary, commandLine):
        ppcmd = [compilerBinary, "/EP"]
        ppcmd += [arg for arg in commandLine if arg not in ("-c", "/c")]
        preprocessor = Popen(ppcmd, stdout=PIPE, stderr=PIPE)
        (preprocessedSourceCode, pperr) = preprocessor.communicate()

        if preprocessor.returncode != 0:
            sys.stderr.write(pperr)
            sys.stderr.write("clcache: preprocessor failed\n")
            sys.exit(preprocessor.returncode)

        compilerHash = getCompilerHash(compilerBinary)
        normalizedCmdLine = ObjectCache._normalizedCommandLine(commandLine)

        h = HashAlgorithm()
        h.update(compilerHash.encode("UTF-8"))
        h.update(' '.join(normalizedCmdLine).encode("UTF-8"))
        h.update(preprocessedSourceCode)
        return h.hexdigest()

    @staticmethod
    def getHash(dataString):
        hasher = HashAlgorithm()
        hasher.update(dataString.encode("UTF-8"))
        return hasher.hexdigest()

    @staticmethod
    def getKeyInManifest(listOfHeaderHashes):
        return ObjectCache.getHash(','.join(listOfHeaderHashes))

    @staticmethod
    def getDirectCacheKey(manifestHash, keyInManifest):
        # We must take into account manifestHash to avoid
        # collisions when different source files use the same
        # set of includes.
        return ObjectCache.getHash(manifestHash + keyInManifest)

    def hasEntry(self, key):
        return os.path.exists(self.cachedObjectName(key)) or os.path.exists(self._cachedCompilerOutputName(key))

    def setEntry(self, key, objectFileName, compilerOutput, compilerStderr):
        ensureDirectoryExists(self._cacheEntryDir(key))
        if objectFileName is not None:
            copyOrLink(objectFileName, self.cachedObjectName(key))
        with open(self._cachedCompilerOutputName(key), 'wb') as f:
            f.write(compilerOutput.encode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC))
        if compilerStderr != '':
            with open(self._cachedCompilerStderrName(key), 'wb') as f:
                f.write(compilerStderr.encode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC))

    def setManifest(self, manifestHash, manifest):
        ensureDirectoryExists(self._manifestDir(manifestHash))
        with open(self._manifestName(manifestHash), 'wb') as outFile:
            pickle.dump(manifest, outFile)

    def getManifest(self, manifestHash):
        fileName = self._manifestName(manifestHash)
        if not os.path.exists(fileName):
            return None
        try:
            with open(fileName, 'rb') as inFile:
                return pickle.load(inFile)
        except (IOError, pickle.UnpicklingError):
            # - file does not exist or cannot be opened (IOError)?
            # - file is corrupted (pickle.UnpicklingError)
            return None

    def cachedObjectName(self, key):
        return os.path.join(self._cacheEntryDir(key), "object")

    def cachedCompilerOutput(self, key):
        with open(self._cachedCompilerOutputName(key), 'rb') as f:
            return f.read().decode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC)

    def cachedCompilerStderr(self, key):
        fileName = self._cachedCompilerStderrName(key)
        if os.path.exists(fileName):
            with open(fileName, 'rb') as f:
                return f.read().decode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC)
        return ''

    def _cacheEntryDir(self, key):
        return os.path.join(self.objectsDir, key[:2], key)

    def _manifestDir(self, manifestHash):
        return os.path.join(self.manifestsDir, manifestHash[:2])

    def _manifestName(self, manifestHash):
        return os.path.join(self._manifestDir(manifestHash), manifestHash + ".dat")

    def _cachedCompilerOutputName(self, key):
        return os.path.join(self._cacheEntryDir(key), "output.txt")

    def _cachedCompilerStderrName(self, key):
        return os.path.join(self._cacheEntryDir(key), "stderr.txt")

    @staticmethod
    def _normalizedCommandLine(cmdline):
        # Remove all arguments from the command line which only influence the
        # preprocessor; the preprocessor's output is already included into the
        # hash sum so we don't have to care about these switches in the
        # command line as well.
        argsToStrip = ("AI", "C", "E", "P", "FI", "u", "X",
                       "FU", "D", "EP", "Fx", "U", "I")

        # Also remove the switch for specifying the output file name; we don't
        # want two invocations which are identical except for the output file
        # name to be treated differently.
        argsToStrip += ("Fo",)

        return [arg for arg in cmdline
                if not (arg[0] in "/-" and arg[1:].startswith(argsToStrip))]


class PersistentJSONDict(object):
    def __init__(self, fileName):
        self._dirty = False
        self._dict = {}
        self._fileName = fileName
        try:
            with open(self._fileName, 'r') as f:
                self._dict = json.load(f)
        except IOError:
            pass

    def save(self):
        if self._dirty:
            with open(self._fileName, 'w') as f:
                json.dump(self._dict, f, sort_keys=True, indent=4)

    def __setitem__(self, key, value):
        self._dict[key] = value
        self._dirty = True

    def __getitem__(self, key):
        return self._dict[key]

    def __contains__(self, key):
        return key in self._dict


class Configuration(object):
    _defaultValues = {"MaximumCacheSize": 1073741824} # 1 GiB

    def __init__(self, objectCache):
        self._objectCache = objectCache
        self._cfg = PersistentJSONDict(os.path.join(objectCache.cacheDirectory(),
                                                    "config.txt"))
        for setting, defaultValue in self._defaultValues.items():
            if setting not in self._cfg:
                self._cfg[setting] = defaultValue

    def maximumCacheSize(self):
        return self._cfg["MaximumCacheSize"]

    def setMaximumCacheSize(self, size):
        self._cfg["MaximumCacheSize"] = size

    def save(self):
        self._cfg.save()


class CacheStatistics(object):
    def __init__(self, objectCache):
        self.objectCache = objectCache
        self._stats = PersistentJSONDict(os.path.join(objectCache.cacheDirectory(),
                                                      "stats.txt"))
        for k in ["CallsWithoutSourceFile",
                  "CallsWithMultipleSourceFiles",
                  "CallsWithPch",
                  "CallsForLinking",
                  "CallsForExternalDebugInfo",
                  "CacheEntries", "CacheSize",
                  "CacheHits", "CacheMisses",
                  "EvictedMisses", "HeaderChangedMisses",
                  "SourceChangedMisses"]:
            if k not in self._stats:
                self._stats[k] = 0

    def numCallsWithoutSourceFile(self):
        return self._stats["CallsWithoutSourceFile"]

    def registerCallWithoutSourceFile(self):
        self._stats["CallsWithoutSourceFile"] += 1

    def numCallsWithMultipleSourceFiles(self):
        return self._stats["CallsWithMultipleSourceFiles"]

    def registerCallWithMultipleSourceFiles(self):
        self._stats["CallsWithMultipleSourceFiles"] += 1

    def numCallsWithPch(self):
        return self._stats["CallsWithPch"]

    def registerCallWithPch(self):
        self._stats["CallsWithPch"] += 1

    def numCallsForLinking(self):
        return self._stats["CallsForLinking"]

    def registerCallForLinking(self):
        self._stats["CallsForLinking"] += 1

    def numCallsForExternalDebugInfo(self):
        return self._stats["CallsForExternalDebugInfo"]

    def registerCallForExternalDebugInfo(self):
        self._stats["CallsForExternalDebugInfo"] += 1

    def numEvictedMisses(self):
        return self._stats["EvictedMisses"]

    def registerEvictedMiss(self):
        self.registerCacheMiss()
        self._stats["EvictedMisses"] += 1

    def numHeaderChangedMisses(self):
        return self._stats["HeaderChangedMisses"]

    def registerHeaderChangedMiss(self):
        self.registerCacheMiss()
        self._stats["HeaderChangedMisses"] += 1

    def numSourceChangedMisses(self):
        return self._stats["SourceChangedMisses"]

    def registerSourceChangedMiss(self):
        self.registerCacheMiss()
        self._stats["SourceChangedMisses"] += 1

    def numCacheEntries(self):
        return self._stats["CacheEntries"]

    def setNumCacheEntries(self, number):
        self._stats["CacheEntries"] = number

    def registerCacheEntry(self, size):
        self._stats["CacheEntries"] += 1
        self._stats["CacheSize"] += size

    def unregisterCacheEntry(self, size):
        self._stats["CacheEntries"] -= 1
        self._stats["CacheSize"] -= size

    def currentCacheSize(self):
        return self._stats["CacheSize"]

    def setCacheSize(self, size):
        self._stats["CacheSize"] = size

    def numCacheHits(self):
        return self._stats["CacheHits"]

    def registerCacheHit(self):
        self._stats["CacheHits"] += 1

    def numCacheMisses(self):
        return self._stats["CacheMisses"]

    def registerCacheMiss(self):
        self._stats["CacheMisses"] += 1

    def resetCounters(self):
        for k in ["CallsWithoutSourceFile",
                  "CallsWithMultipleSourceFiles",
                  "CallsWithPch",
                  "CallsForLinking",
                  "CallsForExternalDebugInfo",
                  "CacheHits", "CacheMisses",
                  "EvictedMisses", "HeaderChangedMisses",
                  "SourceChangedMisses"]:
            self._stats[k] = 0

    def save(self):
        self._stats.save()


class AnalysisResult(object):
    Ok, NoSourceFile, MultipleSourceFilesSimple, \
        MultipleSourceFilesComplex, CalledForLink, \
        CalledWithPch, ExternalDebugInfo = list(range(7))


def getCompilerHash(compilerBinary):
    stat = os.stat(compilerBinary)
    data = '|'.join([
        str(stat.st_mtime),
        str(stat.st_size),
        VERSION,
        ])
    hasher = HashAlgorithm()
    hasher.update(data.encode("UTF-8"))
    return hasher.hexdigest()


def getFileHash(filePath, additionalData=None):
    hasher = HashAlgorithm()
    with open(filePath, 'rb') as inFile:
        hasher.update(inFile.read())
    if additionalData is not None:
        # Encoding of this additional data does not really matter
        # as long as we keep it fixed, otherwise hashes change.
        # The string should fit into ASCII, so UTF8 should not change anything
        hasher.update(additionalData.encode("UTF-8"))
    return hasher.hexdigest()


def getRelFileHash(filePath, baseDir):
    absFilePath = filePath
    if absFilePath.startswith(BASEDIR_REPLACEMENT):
        if not baseDir:
            raise LogicException('No CLCACHE_BASEDIR set, but found relative path ' + filePath)
        absFilePath = absFilePath.replace(BASEDIR_REPLACEMENT, baseDir, 1)
    if not os.path.exists(absFilePath):
        return None
    return getFileHash(absFilePath)


def ensureDirectoryExists(path):
    try:
        os.makedirs(path)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise


def copyOrLink(srcFilePath, dstFilePath):
    ensureDirectoryExists(os.path.dirname(os.path.abspath(dstFilePath)))

    if "CLCACHE_HARDLINK" in os.environ:
        ret = windll.kernel32.CreateHardLinkW(str(dstFilePath), str(srcFilePath), None)
        if ret != 0:
            # Touch the time stamp of the new link so that the build system
            # doesn't confused by a potentially old time on the file. The
            # hard link gets the same timestamp as the cached file.
            # Note that touching the time stamp of the link also touches
            # the time stamp on the cache (and hence on all over hard
            # links). This shouldn't be a problem though.
            os.utime(dstFilePath, None)
            return

    # If hardlinking fails for some reason (or it's not enabled), just
    # fall back to moving bytes around. Always to a temporary path first to
    # lower the chances of corrupting it.
    tempDst = dstFilePath + '.tmp'
    copyfile(srcFilePath, tempDst)
    os.rename(tempDst, dstFilePath)


def myExecutablePath():
    assert hasattr(sys, "frozen"), "is not frozen by py2exe"
    if sys.version_info >= (3, 0):
        return sys.executable.upper()
    else:
        return unicode(sys.executable, sys.getfilesystemencoding()).upper()


def findCompilerBinary():
    if "CLCACHE_CL" in os.environ:
        path = os.environ["CLCACHE_CL"]
        return path if os.path.exists(path) else None

    frozenByPy2Exe = hasattr(sys, "frozen")

    for p in os.environ["PATH"].split(os.pathsep):
        path = os.path.join(p, "cl.exe")
        if os.path.exists(path):
            if not frozenByPy2Exe:
                return path

            # Guard against recursively calling ourselves
            if path.upper() != myExecutablePath():
                return path
    return None


def printTraceStatement(msg):
    if "CLCACHE_LOG" in os.environ:
        scriptDir = os.path.realpath(os.path.dirname(sys.argv[0]))
        print(os.path.join(scriptDir, "clcache.py") + " " + msg)


class CommandLineTokenizer(object):
    def __init__(self, content):
        self.argv = []
        self._content = content
        self._pos = 0
        self._token = ''
        self._parser = self._initialState

        while self._pos < len(self._content):
            self._parser = self._parser(self._content[self._pos])
            self._pos += 1

        if self._token:
            self.argv.append(self._token)

    def _initialState(self, currentChar):
        if currentChar.isspace():
            return self._initialState

        if currentChar == '"':
            return self._quotedState

        if currentChar == '\\':
            self._parseBackslash()
            return self._unquotedState

        self._token += currentChar
        return self._unquotedState

    def _unquotedState(self, currentChar):
        if currentChar.isspace():
            self.argv.append(self._token)
            self._token = ''
            return self._initialState

        if currentChar == '"':
            return self._quotedState

        if currentChar == '\\':
            self._parseBackslash()
            return self._unquotedState

        self._token += currentChar
        return self._unquotedState

    def _quotedState(self, currentChar):
        if currentChar == '"':
            return self._unquotedState

        if currentChar == '\\':
            self._parseBackslash()
            return self._quotedState

        self._token += currentChar
        return self._quotedState

    def _parseBackslash(self):
        numBackslashes = 0
        while self._pos < len(self._content) and self._content[self._pos] == '\\':
            self._pos += 1
            numBackslashes += 1

        followedByDoubleQuote = self._pos < len(self._content) and self._content[self._pos] == '"'
        if followedByDoubleQuote:
            self._token += '\\' * (numBackslashes // 2)
            if numBackslashes % 2 == 0:
                self._pos -= 1
            else:
                self._token += '"'
        else:
            self._token += '\\' * numBackslashes
            self._pos -= 1


def splitCommandsFile(content):
    return CommandLineTokenizer(content).argv


def expandCommandLine(cmdline):
    ret = []

    for arg in cmdline:
        if arg[0] == '@':
            includeFile = arg[1:]
            with open(includeFile, 'rb') as f:
                rawBytes = f.read()

            encoding = None

            encodingByBom = {
                codecs.BOM_UTF32_BE: 'utf-32-be',
                codecs.BOM_UTF32_LE: 'utf-32-le',
                codecs.BOM_UTF16_BE: 'utf-16-be',
                codecs.BOM_UTF16_LE: 'utf-16-le',
            }

            for bom, _ in list(encodingByBom.items()):
                if rawBytes.startswith(bom):
                    encoding = encodingByBom[bom]
                    rawBytes = rawBytes[len(bom):]
                    break

            if encoding:
                includeFileContents = rawBytes.decode(encoding)
            else:
                includeFileContents = rawBytes.decode("UTF-8")

            ret.extend(expandCommandLine(splitCommandsFile(includeFileContents.strip())))
        else:
            ret.append(arg)

    return ret


class CommandLineAnalyzer(object):

    @staticmethod
    def _preprocessToStdout(options):
        # Note: For MSVS 2013 and 2015 there is a documentation bug in
        # https://msdn.microsoft.com/en-us/library/becb7sys.aspx stating that
        # "To send the preprocessed output to stdout, with #line directives,
        # use /P and /EP together."
        # The actual compiler behavior is that both /P and /P /EP go to file.
        # /P is with #line annotations and /P /EP is without.
        return ('E' in options or 'EP' in options) and 'P' not in options

    @staticmethod
    def _parseOptionsAndFiles(cmdline):
        optionsWithParameter = ['Ob', 'Gs', 'Fa', 'Fd', 'Fm',
                                'Fp', 'FR', 'doc', 'FA', 'Fe',
                                'Fo', 'Fr', 'AI', 'FI', 'FU',
                                'D', 'U', 'I', 'Zp', 'vm',
                                'MP', 'Tc', 'V', 'wd', 'wo',
                                'W', 'Yc', 'Yl', 'Tp', 'we',
                                'Yu', 'Zm', 'F', 'Fi']
        options = defaultdict(list)
        sourceFiles = []
        i = 0
        while i < len(cmdline):
            arg = cmdline[i]

            # Plain arguments starting with / or -
            if arg[0] == '/' or arg[0] == '-':
                isParametrized = False
                for opt in optionsWithParameter:
                    if arg[1:len(opt) + 1] == opt:
                        isParametrized = True
                        key = opt
                        if len(arg) > len(opt) + 1:
                            value = arg[len(opt) + 1:]
                        else:
                            value = cmdline[i + 1]
                            i += 1
                        options[key].append(value)
                        break

                if not isParametrized:
                    options[arg[1:]] = []

            # Response file
            elif arg[0] == '@':
                raise AssertionError("No response file arguments (starting with @) must be left here.")

            # Source file arguments
            else:
                sourceFiles.append(arg)

            i += 1

        return options, sourceFiles

    @staticmethod
    def analyze(cmdline):
        options, sourceFiles = CommandLineAnalyzer._parseOptionsAndFiles(cmdline)
        compl = False
        if 'Tp' in options:
            sourceFiles += options['Tp']
            compl = True
        if 'Tc' in options:
            sourceFiles += options['Tc']
            compl = True

        preprocessing = False
        for opt in ['E', 'EP', 'P']:
            if opt in options:
                preprocessing = True
                break

        if len(sourceFiles) == 0:
            return AnalysisResult.NoSourceFile, None, None

        # Technically, it would be possible to support /Zi: we'd just need to
        # copy the generated .pdb files into/out of the cache.
        if 'Zi' in options:
            return AnalysisResult.ExternalDebugInfo, None, None

        if 'Yc' in options or 'Yu' in options:
            return AnalysisResult.CalledWithPch, None, None

        if 'link' in options or ('c' not in options and not preprocessing):
            return AnalysisResult.CalledForLink, None, None

        if len(sourceFiles) > 1:
            if compl:
                return AnalysisResult.MultipleSourceFilesComplex, None, None
            return AnalysisResult.MultipleSourceFilesSimple, sourceFiles, None

        if preprocessing:
            if CommandLineAnalyzer._preprocessToStdout(options):
                outputFile = None
            else:
                # Preprocess to file
                if 'Fi' in options:
                    outputFile = options['Fi'][0]
                else:
                    outputFile = basenameWithoutExtension(sourceFiles[0]) + ".i"
        else:
            if 'Fo' in options:
                outputFile = options['Fo'][0]
                outputFile = os.path.normpath(outputFile)

                if os.path.isdir(outputFile):
                    outputFile = os.path.join(outputFile, basenameWithoutExtension(sourceFiles[0]) + ".obj")
            else:
                outputFile = basenameWithoutExtension(sourceFiles[0]) + ".obj"

        printTraceStatement("Compiler output file: {}".format(outputFile))
        return AnalysisResult.Ok, sourceFiles[0], outputFile


def invokeRealCompiler(compilerBinary, cmdLine, captureOutput=False):
    realCmdline = [compilerBinary] + cmdLine
    printTraceStatement("Invoking real compiler as {}".format(realCmdline))

    returnCode = None
    stdout = ''
    stderr = ''
    if captureOutput:
        compilerProcess = Popen(realCmdline, stdout=PIPE, stderr=PIPE)
        stdoutBinary, stderrBinary = compilerProcess.communicate()
        stdout = stdoutBinary.decode(CL_DEFAULT_CODEC)
        stderr = stderrBinary.decode(CL_DEFAULT_CODEC)
        returnCode = compilerProcess.returncode
    else:
        returnCode = subprocess.call(realCmdline)

    printTraceStatement("Real compiler returned code %d" % returnCode)
    return returnCode, stdout, stderr


# Given a list of Popen objects, removes and returns
# a completed Popen object.
#
# This is a bit inefficient but Python on Windows does not appear to
# provide any blocking "wait for any process to complete" out of the box.
def waitForAnyProcess(procs):
    out = [p for p in procs if p.poll() is not None]
    if len(out) >= 1:
        out = out[0]
        procs.remove(out)
        return out

    # Damn, none finished yet.
    # Do a blocking wait for the first one.
    # This could waste time waiting for one process while others have
    # already finished :(
    out = procs.pop(0)
    out.wait()
    return out


# Returns the amount of jobs which should be run in parallel when
# invoked in batch mode.
#
# The '/MP' option determines this, which may be set in cmdLine or
# in the CL environment variable.
def jobCount(cmdLine):
    switches = []

    if 'CL' in os.environ:
        switches.extend(os.environ['CL'].split(' '))

    switches.extend(cmdLine)

    mpSwitches = [switch for switch in switches if re.search(r'^/MP(\d+)?$', switch) is not None]
    if len(mpSwitches) == 0:
        return 1

    # the last instance of /MP takes precedence
    mpSwitch = mpSwitches.pop()

    count = mpSwitch[3:]
    if count != "":
        return int(count)

    # /MP, but no count specified; use CPU count
    try:
        return multiprocessing.cpu_count()
    except NotImplementedError:
        # not expected to happen
        return 2


# Run commands, up to j concurrently.
# Aborts on first failure and returns the first non-zero exit code.
def runJobs(commands, j=1):
    running = []

    while len(commands):

        while len(running) > j:
            thiscode = waitForAnyProcess(running).returncode
            if thiscode != 0:
                return thiscode

        thiscmd = commands.pop(0)
        running.append(Popen(thiscmd))

    while len(running) > 0:
        thiscode = waitForAnyProcess(running).returncode
        if thiscode != 0:
            return thiscode

    return 0


# re-invoke clcache.py once per source file.
# Used when called via nmake 'batch mode'.
# Returns the first non-zero exit code encountered, or 0 if all jobs succeed.
def reinvokePerSourceFile(cmdLine, sourceFiles):
    printTraceStatement("Will reinvoke self for: {}".format(sourceFiles))
    commands = []
    for sourceFile in sourceFiles:
        # The child command consists of clcache.py ...
        newCmdLine = [sys.executable]
        if not hasattr(sys, "frozen"):
            newCmdLine.append(sys.argv[0])

        for arg in cmdLine:
            # and the current source file ...
            if arg == sourceFile:
                newCmdLine.append(arg)
            # and all other arguments which are not a source file
            elif arg not in sourceFiles:
                newCmdLine.append(arg)

        printTraceStatement("Child: {}".format(newCmdLine))
        commands.append(newCmdLine)

    return runJobs(commands, jobCount(cmdLine))

def printStatistics(cache):
    cfg = Configuration(cache)
    stats = CacheStatistics(cache)
    out = """
clcache statistics:
  current cache dir         : {}
  cache size                : {:,} bytes
  maximum cache size        : {:,} bytes
  cache entries             : {}
  cache hits                : {}
  cache misses
    total                      : {}
    evicted                    : {}
    header changed             : {}
    source changed             : {}
  passed to real compiler
    called for linking         : {}
    called for external debug  : {}
    called w/o source          : {}
    called w/ multiple sources : {}
    called w/ PCH              : {}""".strip().format(
        cache.cacheDirectory(),
        stats.currentCacheSize(),
        cfg.maximumCacheSize(),
        stats.numCacheEntries(),
        stats.numCacheHits(),
        stats.numCacheMisses(),
        stats.numEvictedMisses(),
        stats.numHeaderChangedMisses(),
        stats.numSourceChangedMisses(),
        stats.numCallsForLinking(),
        stats.numCallsForExternalDebugInfo(),
        stats.numCallsWithoutSourceFile(),
        stats.numCallsWithMultipleSourceFiles(),
        stats.numCallsWithPch(),
    )
    print(out)

def resetStatistics(cache):
    stats = CacheStatistics(cache)
    stats.resetCounters()
    stats.save()
    print('Statistics reset')

def cleanCache(cache):
    cfg = Configuration(cache)
    stats = CacheStatistics(cache)
    cache.clean(stats, cfg.maximumCacheSize())
    stats.save()
    print('Cache cleaned')

def clearCache(cache):
    stats = CacheStatistics(cache)
    cache.clean(stats, 0)
    stats.save()
    print('Cache cleared')


# Returns pair - list of includes and new compiler output.
# Output changes if strip is True in that case all lines with include
# directives are stripped from it
def parseIncludesList(compilerOutput, sourceFile, baseDir, strip):
    newOutput = []
    includesSet = set([])

    # Example lines
    # Note: including file:         C:\Program Files (x86)\Microsoft Visual Studio 12.0\VC\INCLUDE\limits.h
    # Hinweis: Einlesen der Datei:   C:\Program Files (x86)\Microsoft Visual Studio 12.0\VC\INCLUDE\iterator
    #
    # So we match
    # - one word (translation of "note")
    # - colon
    # - space
    # - a phrase containing characters and spaces (translation of "including file")
    # - colon
    # - one or more spaces
    # - the file path, starting with a non-whitespace character
    reFilePath = re.compile(r'^(\w+): ([ \w]+):( +)(?P<file_path>\S.*)$')

    absSourceFile = os.path.normcase(os.path.abspath(sourceFile))
    if baseDir:
        baseDir = os.path.normcase(baseDir)
    for line in compilerOutput.splitlines(True):
        match = reFilePath.match(line.rstrip('\r\n'))
        if match is not None:
            filePath = match.group('file_path')
            filePath = os.path.normcase(os.path.abspath(filePath))
            if filePath != absSourceFile:
                if baseDir and filePath.startswith(baseDir):
                    filePath = filePath.replace(baseDir, BASEDIR_REPLACEMENT, 1)
                includesSet.add(filePath)
        elif strip:
            newOutput.append(line)
    if strip:
        return list(includesSet), ''.join(newOutput)
    else:
        return list(includesSet), compilerOutput


def addObjectToCache(stats, cache, outputFile, compilerStdout, compilerStderr, cachekey):
    printTraceStatement("Adding file {} to cache using key {}".format(outputFile, cachekey))
    cache.setEntry(cachekey, outputFile, compilerStdout, compilerStderr)
    if outputFile is not None:
        stats.registerCacheEntry(os.path.getsize(outputFile))
        cfg = Configuration(cache)
        cache.clean(stats, cfg.maximumCacheSize())


def processCacheHit(cache, outputFile, cachekey):
    stats = CacheStatistics(cache)
    stats.registerCacheHit()
    stats.save()
    printTraceStatement("Reusing cached object for key {} for output file {}".format(cachekey, outputFile))
    if outputFile is not None:
        if os.path.exists(outputFile):
            os.remove(outputFile)
        copyOrLink(cache.cachedObjectName(cachekey), outputFile)
    compilerOutput = cache.cachedCompilerOutput(cachekey)
    compilerStderr = cache.cachedCompilerStderr(cachekey)
    printTraceStatement("Finished. Exit code 0")
    return 0, compilerOutput, compilerStderr


def postprocessObjectEvicted(cache, outputFile, cachekey, compilerResult):
    printTraceStatement("Cached object already evicted for key {} for output file {}".format(cachekey, outputFile))
    returnCode, compilerOutput, compilerStderr = compilerResult

    with cache.lock:
        stats = CacheStatistics(cache)
        stats.registerEvictedMiss()
        if returnCode == 0 and (outputFile is None or os.path.exists(outputFile)):
            addObjectToCache(stats, cache, outputFile, compilerOutput, compilerStderr, cachekey)
        stats.save()

    return compilerResult


def postprocessHeaderChangedMiss(cache, outputFile, manifest, manifestHash, keyInManifest, compilerResult):
    cachekey = ObjectCache.getDirectCacheKey(manifestHash, keyInManifest)
    returnCode, compilerOutput, compilerStderr = compilerResult

    removedItems = []
    if returnCode == 0 and (outputFile is None or os.path.exists(outputFile)):
        while len(manifest.hashes) >= MAX_MANIFEST_HASHES:
            _, objectHash = manifest.hashes.popitem()
            removedItems.append(objectHash)
        manifest.hashes[keyInManifest] = cachekey

    with cache.lock:
        stats = CacheStatistics(cache)
        stats.registerHeaderChangedMiss()
        if returnCode == 0 and (outputFile is None or os.path.exists(outputFile)):
            addObjectToCache(stats, cache, outputFile, compilerOutput, compilerStderr, cachekey)
            cache.removeObjects(stats, removedItems)
            cache.setManifest(manifestHash, manifest)
        stats.save()

    return compilerResult


def postprocessNoManifestMiss(
        cache, outputFile, manifestHash, baseDir, cmdLine, sourceFile, compilerResult, stripIncludes):
    returnCode, compilerOutput, compilerStderr = compilerResult
    grabStderr = False
    # If these options present, cl.exe will list includes on stderr, not stdout
    for option in ['/E', '/EP', '/P']:
        if option in cmdLine:
            grabStderr = True
            break
    if grabStderr:
        listOfIncludes, compilerStderr = parseIncludesList(compilerStderr, sourceFile, baseDir, stripIncludes)
    else:
        listOfIncludes, compilerOutput = parseIncludesList(compilerOutput, sourceFile, baseDir, stripIncludes)

    manifest = None
    cachekey = None

    if returnCode == 0 and (outputFile is None or os.path.exists(outputFile)):
        # Store compile output and manifest
        manifest = Manifest(listOfIncludes, {})
        listOfHeaderHashes = [getRelFileHash(fileName, baseDir) for fileName in listOfIncludes]
        keyInManifest = ObjectCache.getKeyInManifest(listOfHeaderHashes)
        cachekey = ObjectCache.getDirectCacheKey(manifestHash, keyInManifest)
        manifest.hashes[keyInManifest] = cachekey

    with cache.lock:
        stats = CacheStatistics(cache)
        stats.registerSourceChangedMiss()
        if returnCode == 0 and (outputFile is None or os.path.exists(outputFile)):
            # Store compile output and manifest
            addObjectToCache(stats, cache, outputFile, compilerOutput, compilerStderr, cachekey)
            cache.setManifest(manifestHash, manifest)
        stats.save()

    return returnCode, compilerOutput, compilerStderr


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--help":
        print("""
clcache.py v{}
  --help    : show this help
  -s        : print cache statistics
  -c        : clean cache
  -C        : clear cache
  -z        : reset cache statistics
  -M <size> : set maximum cache size (in bytes)
""".strip().format(VERSION))
        return 0

    cache = ObjectCache()

    if len(sys.argv) == 2 and sys.argv[1] == "-s":
        with cache.lock:
            printStatistics(cache)
        return 0

    if len(sys.argv) == 2 and sys.argv[1] == "-c":
        with cache.lock:
            cleanCache(cache)
        return 0

    if len(sys.argv) == 2 and sys.argv[1] == "-C":
        with cache.lock:
            clearCache(cache)
        return 0

    if len(sys.argv) == 2 and sys.argv[1] == "-z":
        with cache.lock:
            resetStatistics(cache)
        return 0

    if len(sys.argv) == 3 and sys.argv[1] == "-M":
        arg = sys.argv[2]
        try:
            maxSizeValue = int(arg)
        except ValueError:
            print("Given max size argument is not a valid integer: '{}'.".format(arg), file=sys.stderr)
            return 1
        if maxSizeValue < 1:
            print("Max size argument must be greater than 0.", file=sys.stderr)
            return 1

        with cache.lock:
            cfg = Configuration(cache)
            cfg.setMaximumCacheSize(maxSizeValue)
            cfg.save()
        return 0

    compiler = findCompilerBinary()
    if not compiler:
        print("Failed to locate cl.exe on PATH (and CLCACHE_CL is not set), aborting.")
        return 1

    printTraceStatement("Found real compiler binary at '%s'" % compiler)

    if "CLCACHE_DISABLE" in os.environ:
        return invokeRealCompiler(compiler, sys.argv[1:])[0]
    try:
        exitCode, compilerStdout, compilerStderr = processCompileRequest(cache, compiler, sys.argv)
        printBinary(compilerStdout.encode(CL_DEFAULT_CODEC), stream=sys.stdout)
        printBinary(compilerStderr.encode(CL_DEFAULT_CODEC), stream=sys.stderr)
        return exitCode
    except LogicException as e:
        print(e)
        return 1


def processCompileRequest(cache, compiler, args):
    printTraceStatement("Parsing given commandline '%s'" % args[1:])

    cmdLine = expandCommandLine(sys.argv[1:])
    printTraceStatement("Expanded commandline '%s'" % cmdLine)
    analysisResult, sourceFile, outputFile = CommandLineAnalyzer.analyze(cmdLine)

    if analysisResult == AnalysisResult.MultipleSourceFilesSimple:
        return reinvokePerSourceFile(cmdLine, sourceFile), '', ''

    if analysisResult != AnalysisResult.Ok:
        with cache.lock:
            stats = CacheStatistics(cache)
            if analysisResult == AnalysisResult.NoSourceFile:
                printTraceStatement("Cannot cache invocation as {}: no source file found".format(cmdLine))
                stats.registerCallWithoutSourceFile()
            elif analysisResult == AnalysisResult.MultipleSourceFilesComplex:
                printTraceStatement("Cannot cache invocation as {}: multiple source files found".format(cmdLine))
                stats.registerCallWithMultipleSourceFiles()
            elif analysisResult == AnalysisResult.CalledWithPch:
                printTraceStatement("Cannot cache invocation as {}: precompiled headers in use".format(cmdLine))
                stats.registerCallWithPch()
            elif analysisResult == AnalysisResult.CalledForLink:
                printTraceStatement("Cannot cache invocation as {}: called for linking".format(cmdLine))
                stats.registerCallForLinking()
            elif analysisResult == AnalysisResult.ExternalDebugInfo:
                printTraceStatement(
                    "Cannot cache invocation as {}: external debug information (/Zi) is not supported".format(cmdLine)
                )
                stats.registerCallForExternalDebugInfo()
            stats.save()
        return invokeRealCompiler(compiler, args[1:])

    if 'CLCACHE_NODIRECT' in os.environ:
        return processNoDirect(cache, outputFile, compiler, cmdLine)
    else:
        return processDirect(cache, outputFile, compiler, cmdLine, sourceFile)


def processDirect(cache, outputFile, compiler, cmdLine, sourceFile):
    manifestHash = ObjectCache.getManifestHash(compiler, cmdLine, sourceFile)
    with cache.lock:
        manifest = cache.getManifest(manifestHash)
        baseDir = os.environ.get('CLCACHE_BASEDIR')
        if baseDir and not baseDir.endswith(os.path.sep):
            baseDir += os.path.sep
        if manifest is not None:
            # NOTE: command line options already included in hash for manifest name
            listOfHeaderHashes = []
            for fileName in manifest.includeFiles:
                fileHash = getRelFileHash(fileName, baseDir)
                if fileHash is not None:
                    # May be if source does not use this header anymore (e.g. if that
                    # header was included through some other header, which now changed).
                    listOfHeaderHashes.append(fileHash)
            keyInManifest = ObjectCache.getKeyInManifest(listOfHeaderHashes)
            cachekey = manifest.hashes.get(keyInManifest)
            if cachekey is not None:
                if cache.hasEntry(cachekey):
                    return processCacheHit(cache, outputFile, cachekey)
                else:
                    postProcessing = lambda compilerResult: postprocessObjectEvicted(
                        cache, outputFile, cachekey, compilerResult)
            else:
                postProcessing = lambda compilerResult: postprocessHeaderChangedMiss(
                    cache, outputFile, manifest, manifestHash, keyInManifest, compilerResult)
        else:
            origCmdLine = cmdLine
            stripIncludes = False
            if '/showIncludes' not in cmdLine:
                cmdLine = ['/showIncludes'] + cmdLine
                stripIncludes = True
            postProcessing = lambda compilerResult: postprocessNoManifestMiss(
                cache, outputFile, manifestHash, baseDir, origCmdLine, sourceFile, compilerResult, stripIncludes)

    compilerResult = invokeRealCompiler(compiler, cmdLine, captureOutput=True)
    compilerResult = postProcessing(compilerResult)
    printTraceStatement("Finished. Exit code %d" % compilerResult[0])
    return compilerResult


def processNoDirect(cache, outputFile, compiler, cmdLine):
    cachekey = ObjectCache.computeKey(compiler, cmdLine)
    with cache.lock:
        if cache.hasEntry(cachekey):
            stats = CacheStatistics(cache)
            stats.registerCacheHit()
            stats.save()
            printTraceStatement("Reusing cached object for key {} for output file {}".format(cachekey, outputFile))
            if outputFile is not None and os.path.exists(outputFile):
                os.remove(outputFile)
            copyOrLink(cache.cachedObjectName(cachekey), outputFile)
            compilerStdout = cache.cachedCompilerOutput(cachekey)
            compilerStderr = cache.cachedCompilerStderr(cachekey)
            printTraceStatement("Finished. Exit code 0")
            return 0, compilerStdout, compilerStderr

    returnCode, compilerStdout, compilerStderr = invokeRealCompiler(compiler, cmdLine, captureOutput=True)
    with cache.lock:
        stats = CacheStatistics(cache)
        stats.registerCacheMiss()
        if returnCode == 0 and outputFile is not None and os.path.exists(outputFile):
            addObjectToCache(stats, cache, outputFile, compilerStdout, compilerStderr, cachekey)
        stats.save()

    printTraceStatement("Finished. Exit code %d" % returnCode)
    return returnCode, compilerStdout, compilerStderr

if __name__ == '__main__':
    sys.exit(main())
