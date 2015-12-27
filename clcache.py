#!/usr/bin/env python
#
# clcache.py - a compiler cache for Microsoft Visual Studio
#
# Copyright (c) 2010, 2011, 2012, 2013 froglogic GmbH <raabe@froglogic.com>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the <organization> nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
from ctypes import windll, wintypes
import codecs
from collections import defaultdict, namedtuple
import cPickle as pickle
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

HASH_ALGORITHM = hashlib.md5

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


class ObjectCacheLockException(Exception):
    pass


class LogicException(Exception):
    def __init__(self, message):
        self.value = message

    def __str__(self):
        return repr(self.message)


class ObjectCacheLock:
    """ Implements a lock for the object cache which
    can be used in 'with' statements. """
    INFINITE = 0xFFFFFFFF

    def __init__(self, mutexName, timeoutMs):
        mutexName = 'Local\\' + mutexName
        self._mutex = windll.kernel32.CreateMutexW(
            wintypes.c_int(0),
            wintypes.c_int(0),
            unicode(mutexName))
        self._timeoutMs = timeoutMs
        self._acquired = False
        assert self._mutex

    def __enter__(self):
        if not self._acquired:
            self.acquire()

    def __exit__(self, type, value, traceback):
        if self._acquired:
            self.release()

    def __del__(self):
        windll.kernel32.CloseHandle(self._mutex)

    def acquire(self):
        WAIT_ABANDONED = 0x00000080
        result = windll.kernel32.WaitForSingleObject(
            self._mutex, wintypes.c_int(self._timeoutMs))
        if result != 0 and result != WAIT_ABANDONED:
            errorString = 'Error! WaitForSingleObject returns {result}, last error {error}'.format(
                result=result,
                error=windll.kernel32.GetLastError())
            raise ObjectCacheLockException(errorString)
        self._acquired = True

    def release(self):
        windll.kernel32.ReleaseMutex(self._mutex)
        self._acquired = False


class ObjectCache:
    def __init__(self):
        try:
            self.dir = os.environ["CLCACHE_DIR"]
        except KeyError:
            self.dir = os.path.join(os.path.expanduser("~"), "clcache")
        if not os.path.exists(self.dir):
            os.makedirs(self.dir)
        self.manifestsDir = os.path.join(self.dir, "manifests")
        if not os.path.exists(self.manifestsDir):
            os.makedirs(self.manifestsDir)
        self.objectsDir = os.path.join(self.dir, "objects")
        if not os.path.exists(self.objectsDir):
            os.makedirs(self.objectsDir)
        lockName = self.cacheDirectory().replace(':', '-').replace('\\', '-')
        self.lock = ObjectCacheLock(lockName, 10 * 1000)

    def cacheDirectory(self):
        return self.dir

    def clean(self, stats, maximumSize):
        with self.lock:
            currentSize = stats.currentCacheSize()
            if currentSize < maximumSize:
                return

            # Free at least 10% to avoid cleaning up too often which
            # is a big performance hit with large caches.
            effectiveMaximumSize = maximumSize * 0.9

            objects = [os.path.join(root, "object")
                       for root, folder, files in os.walk(self.objectsDir)
                       if "object" in files]

            objectInfos = [(os.stat(fn), fn) for fn in objects]
            objectInfos.sort(key=lambda t: t[0].st_atime)

            for stat, fn in objectInfos:
                rmtree(os.path.split(fn)[0])
                currentSize -= stat.st_size
                if currentSize < effectiveMaximumSize:
                    break

            stats.setCacheSize(currentSize)

    def removeObjects(self, stats, removedObjects):
        if len(removedObjects) == 0:
            return
        with self.lock:
            currentSize = stats.currentCacheSize()
            for hash in removedObjects:
                dirPath = self._cacheEntryDir(hash)
                if not os.path.exists(dirPath):
                    continue  # May be if object already evicted.
                objectPath = os.path.join(dirPath, "object")
                if os.path.exists(objectPath):
                    # May be absent if this if cached compiler
                    # output (for preprocess-only).
                    fileStat = os.stat(objectPath)
                    currentSize -= fileStat.st_size
                rmtree(dirPath)
            stats.setCacheSize(currentSize)

    def getManifestHash(self, compilerBinary, commandLine, sourceFile):
        stat = os.stat(compilerBinary)
        # NOTE: We intentionally do not normalize command line to include
        # preprocessor options. In direct mode we do not perform
        # preprocessing before cache lookup, so all parameters are important
        additionalData = '{mtime}{size}{cmdLine}'.format(
            mtime=stat.st_mtime,
            size=stat.st_size,
            cmdLine=' '.join(commandLine))
        return getFileHash(sourceFile, additionalData)

    def computeKey(self, compilerBinary, commandLine):
        ppcmd = [compilerBinary, "/EP"]
        ppcmd += [arg for arg in commandLine if arg not in ("-c", "/c")]
        preprocessor = Popen(ppcmd, stdout=PIPE, stderr=PIPE)
        (preprocessedSourceCode, pperr) = preprocessor.communicate()

        if preprocessor.returncode != 0:
            sys.stderr.write(pperr)
            sys.stderr.write("clcache: preprocessor failed\n")
            sys.exit(preprocessor.returncode)

        normalizedCmdLine = self._normalizedCommandLine(commandLine)

        stat = os.stat(compilerBinary)
        h = HASH_ALGORITHM()
        h.update(str(stat.st_mtime))
        h.update(str(stat.st_size))
        h.update(' '.join(normalizedCmdLine))
        h.update(preprocessedSourceCode)
        return h.hexdigest()

    def getKeyInManifest(self, listOfHeaderHashes):
        return getHash(','.join(listOfHeaderHashes))

    def getDirectCacheKey(self, manifestHash, keyInManifest):
        # We must take into account manifestHash to avoid
        # collisions when different source files use the same
        # set of includes.
        return getHash(manifestHash + keyInManifest)

    def hasEntry(self, key):
        with self.lock:
            return os.path.exists(self.cachedObjectName(key)) or os.path.exists(self._cachedCompilerOutputName(key))

    def setEntry(self, key, objectFileName, compilerOutput, compilerStderr):
        with self.lock:
            if not os.path.exists(self._cacheEntryDir(key)):
                os.makedirs(self._cacheEntryDir(key))
            if objectFileName != '':
                copyOrLink(objectFileName, self.cachedObjectName(key))
            open(self._cachedCompilerOutputName(key), 'w').write(compilerOutput)
            if compilerStderr != '':
                open(self._cachedCompilerStderrName(key), 'w').write(compilerStderr)

    def setManifest(self, manifestHash, manifest):
        with self.lock:
            if not os.path.exists(self._manifestDir(manifestHash)):
                os.makedirs(self._manifestDir(manifestHash))
            with open(self._manifestName(manifestHash), 'wb') as outFile:
                pickle.dump(manifest, outFile)

    def getManifest(self, manifestHash):
        with self.lock:
            fileName = self._manifestName(manifestHash)
            if not os.path.exists(fileName):
                return None
            with open(fileName, 'rb') as inFile:
                try:
                    return pickle.load(inFile)
                except:
                    # Seems, file is corrupted
                    return None

    def cachedObjectName(self, key):
        return os.path.join(self._cacheEntryDir(key), "object")

    def cachedCompilerOutput(self, key):
        return open(self._cachedCompilerOutputName(key), 'r').read()

    def cachedCompilerStderr(self, key):
        fileName = self._cachedCompilerStderrName(key)
        if os.path.exists(fileName):
            return open(fileName, 'r').read()
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

    def _normalizedCommandLine(self, cmdline):
        # Remove all arguments from the command line which only influence the
        # preprocessor; the preprocessor's output is already included into the
        # hash sum so we don't have to care about these switches in the
        # command line as well.
        _argsToStrip = ("AI", "C", "E", "P", "FI", "u", "X",
                        "FU", "D", "EP", "Fx", "U", "I")

        # Also remove the switch for specifying the output file name; we don't
        # want two invocations which are identical except for the output file
        # name to be treated differently.
        _argsToStrip += ("Fo",)

        return [arg for arg in cmdline
                if not (arg[0] in "/-" and arg[1:].startswith(_argsToStrip))]


class PersistentJSONDict:
    def __init__(self, fileName):
        self._dirty = False
        self._dict = {}
        self._fileName = fileName
        try:
            self._dict = json.load(open(self._fileName, 'r'))
        except:
            pass

    def save(self):
        if self._dirty:
            json.dump(self._dict, open(self._fileName, 'w'))

    def __setitem__(self, key, value):
        self._dict[key] = value
        self._dirty = True

    def __getitem__(self, key):
        return self._dict[key]

    def __contains__(self, key):
        return key in self._dict


class Configuration:
    _defaultValues = {"MaximumCacheSize": 1073741824} # 1 GiB

    def __init__(self, objectCache):
        self._objectCache = objectCache
        with objectCache.lock:
            self._cfg = PersistentJSONDict(os.path.join(objectCache.cacheDirectory(),
                                                        "config.txt"))
        for setting, defaultValue in self._defaultValues.iteritems():
            if setting not in self._cfg:
                self._cfg[setting] = defaultValue

    def maximumCacheSize(self):
        return self._cfg["MaximumCacheSize"]

    def setMaximumCacheSize(self, size):
        self._cfg["MaximumCacheSize"] = size

    def save(self):
        with self._objectCache.lock:
            self._cfg.save()


class CacheStatistics:
    def __init__(self, objectCache):
        self.objectCache = objectCache
        with objectCache.lock:
            self._stats = PersistentJSONDict(os.path.join(objectCache.cacheDirectory(),
                                                          "stats.txt"))
        for k in ["CallsWithoutSourceFile",
                  "CallsWithMultipleSourceFiles",
                  "CallsWithPch",
                  "CallsForLinking",
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

    def registerCacheEntry(self, size):
        self._stats["CacheEntries"] += 1
        self._stats["CacheSize"] += size

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
                  "CacheHits", "CacheMisses",
                  "EvictedMisses", "HeaderChangedMisses",
                  "SourceChangedMisses"]:
            self._stats[k] = 0

    def save(self):
        with self.objectCache.lock:
            self._stats.save()


class AnalysisResult:
    Ok, NoSourceFile, MultipleSourceFilesSimple, \
        MultipleSourceFilesComplex, CalledForLink, \
        CalledWithPch, ExternalDebugInfo = range(7)


def getFileHash(filePath, additionalData=None):
    hasher = HASH_ALGORITHM()
    with open(filePath, 'rb') as inFile:
        hasher.update(inFile.read())
    if additionalData is not None:
        hasher.update(additionalData)
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


def getHash(data):
    hasher = HASH_ALGORITHM()
    hasher.update(data)
    return hasher.hexdigest()


def copyOrLink(srcFilePath, dstFilePath):
    # Ensure the destination folder exists
    try:
        os.makedirs(os.path.dirname(dstFilePath))
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise

    if "CLCACHE_HARDLINK" in os.environ:
        ret = windll.kernel32.CreateHardLinkW(unicode(dstFilePath), unicode(srcFilePath), None)
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
    # fall back to moving bytes around...
    copyfile(srcFilePath, dstFilePath)


def findCompilerBinary():
    if "CLCACHE_CL" in os.environ:
        path = os.environ["CLCACHE_CL"]
        return path if os.path.exists(path) else None

    frozenByPy2Exe = hasattr(sys, "frozen")
    if frozenByPy2Exe:
        myExecutablePath = unicode(sys.executable, sys.getfilesystemencoding()).upper()

    for dir in os.environ["PATH"].split(os.pathsep):
        path = os.path.join(dir, "cl.exe")
        if os.path.exists(path):
            if not frozenByPy2Exe:
                return path

            # Guard against recursively calling ourselves
            if path.upper() != myExecutablePath:
                return path
    return None


def printTraceStatement(msg):
    if "CLCACHE_LOG" in os.environ:
        script_dir = os.path.realpath(os.path.dirname(sys.argv[0]))
        print os.path.join(script_dir, "clcache.py") + " " + msg


def extractArgument(line, start, end):
    # If there are quotes from both sides of argument, remove them
    # "-Isome path" must becomse -Isome path
    if line[start] == '"' and line[end - 1] == '"' and start != (end - 1):
        start += 1
        end -= 1
    # Unescape quotes.
    return line[start:end].replace('\\"', '"').strip()


def splitCommandsFile(line):
    # Note, we must treat lines in quotes as one argument. We do not use shlex
    # since seems it difficult to set up it to correctly parse escaped quotes.
    # A good test line to split is
    # '"-IC:\\Program files\\Some library" -DX=1 -DVERSION=\\"1.0\\"
    # -I..\\.. -I"..\\..\\lib" -DMYPATH=\\"C:\\Path\\"'
    i = 0
    wordStart = -1
    insideQuotes = False
    result = []
    while i < len(line):
        if line[i] == ' ' and not insideQuotes and wordStart >= 0:
            result.append(extractArgument(line, wordStart, i))
            wordStart = -1
        if line[i] == '"' and ((i == 0) or (i > 0 and line[i - 1] != '\\')):
            insideQuotes = not insideQuotes
        if line[i] != ' ' and wordStart < 0:
            wordStart = i
        i += 1

    if wordStart >= 0:
        result.append(extractArgument(line, wordStart, len(line)))
    return result


def expandCommandLine(cmdline):
    ret = []

    for arg in cmdline:
        if arg[0] == '@':
            includeFile = arg[1:]
            with open(includeFile, 'rb') as file:
                rawBytes = file.read()

            encoding = None

            encodingByBOM = {
                codecs.BOM_UTF32_BE: 'utf-32-be',
                codecs.BOM_UTF32_LE: 'utf-32-le',
                codecs.BOM_UTF16_BE: 'utf-16-be',
                codecs.BOM_UTF16_LE: 'utf-16-le',
            }

            for bom, enc in encodingByBOM.items():
                if rawBytes.startswith(bom):
                    encoding = encodingByBOM[bom]
                    rawBytes = rawBytes[len(bom):]
                    break

            includeFileContents = rawBytes.decode(encoding) if encoding is not None else rawBytes

            ret.extend(expandCommandLine(splitCommandsFile(includeFileContents.strip())))
        else:
            ret.append(arg)

    return ret


def parseCommandLine(cmdline):
    optionsWithParameter = ['Ob', 'Gs', 'Fa', 'Fd', 'Fm',
                            'Fp', 'FR', 'doc', 'FA', 'Fe',
                            'Fo', 'Fr', 'AI', 'FI', 'FU',
                            'D', 'U', 'I', 'Zp', 'vm',
                            'MP', 'Tc', 'V', 'wd', 'wo',
                            'W', 'Yc', 'Yl', 'Tp', 'we',
                            'Yu', 'Zm', 'F', 'Fi']
    options = defaultdict(list)
    responseFile = ""
    sourceFiles = []
    i = 0
    while i < len(cmdline):
        arg = cmdline[i]

        # Plain arguments startign with / or -
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

        # Reponse file
        elif arg[0] == '@':
            responseFile = arg[1:]

        # Source file arguments
        else:
            sourceFiles.append(arg)

        i += 1

    return options, responseFile, sourceFiles


def analyzeCommandLine(cmdline):
    options, responseFile, sourceFiles = parseCommandLine(cmdline)
    compl = False

    # Technically, it would be possible to support /Zi: we'd just need to
    # copy the generated .pdb files into/out of the cache.
    if 'Zi' in options:
        return AnalysisResult.ExternalDebugInfo, None, None
    if 'Yu' in options:
        return AnalysisResult.CalledWithPch, None, None
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

    if 'link' in options or ('c' not in options and not preprocessing):
        return AnalysisResult.CalledForLink, None, None

    if len(sourceFiles) == 0:
        return AnalysisResult.NoSourceFile, None, None

    if len(sourceFiles) > 1:
        if compl:
            return AnalysisResult.MultipleSourceFilesComplex, None, None
        return AnalysisResult.MultipleSourceFilesSimple, sourceFiles, None

    outputFile = None
    if 'Fo' in options:
        outputFile = options['Fo'][0]

        if os.path.isdir(outputFile):
            srcFileName = os.path.basename(sourceFiles[0])
            outputFile = os.path.join(outputFile,
                                      os.path.splitext(srcFileName)[0] + ".obj")
    elif preprocessing:
        if 'P' in options:
            # Prerpocess to file.
            if 'Fi' in options:
                outputFile = options['Fi'][0]
            else:
                srcFileName = os.path.basename(sourceFiles[0])
                outputFile = os.path.join(os.getcwd(),
                                          os.path.splitext(srcFileName)[0] + ".i")
        else:
            # Prerocess to stdout. Use empty string rather then None to ease
            # output to log.
            outputFile = ''
    else:
        srcFileName = os.path.basename(sourceFiles[0])
        outputFile = os.path.join(os.getcwd(),
                                  os.path.splitext(srcFileName)[0] + ".obj")

    # Strip quotes around file names; seems to happen with source files
    # with spaces in their names specified via a response file generated
    # by Visual Studio.
    if outputFile.startswith('"') and outputFile.endswith('"'):
        outputFile = outputFile[1:-1]

    printTraceStatement("Compiler output file: '%s'" % outputFile)
    return AnalysisResult.Ok, sourceFiles[0], outputFile


def invokeRealCompiler(compilerBinary, cmdLine, captureOutput=False):
    realCmdline = [compilerBinary] + cmdLine
    printTraceStatement("Invoking real compiler as '%s'" % ' '.join(realCmdline))

    returnCode = None
    stdout = ''
    stderr = ''
    if captureOutput:
        compilerProcess = Popen(realCmdline, universal_newlines=True, stdout=PIPE, stderr=PIPE)
        stdout, stderr = compilerProcess.communicate()
        returnCode = compilerProcess.returncode
    else:
        returnCode = subprocess.call(realCmdline, universal_newlines=True)

    printTraceStatement("Real compiler returned code %d" % returnCode)
    return returnCode, stdout, stderr


# Given a list of Popen objects, removes and returns
# a completed Popen object.
#
# FIXME: this is a bit inefficient, Python on Windows does not appear
# to provide any blocking "wait for any process to complete" out of the
# box.
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

    mp_switch = [switch for switch in switches if re.search(r'^/MP\d+$', switch) is not None]
    if len(mp_switch) == 0:
        return 1

    # the last instance of /MP takes precedence
    mp_switch = mp_switch.pop()

    count = mp_switch[3:]
    if count != "":
        return int(count)

    # /MP, but no count specified; use CPU count
    try:
        return multiprocessing.cpu_count()
    except:
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

    printTraceStatement("Will reinvoke self for: [%s]" % '] ['.join(sourceFiles))
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

        printTraceStatement("Child: [%s]" % '] ['.join(newCmdLine))
        commands.append(newCmdLine)

    return runJobs(commands, jobCount(cmdLine))


def printStatistics():
    cache = ObjectCache()
    with cache.lock:
        stats = CacheStatistics(cache)
        cfg = Configuration(cache)
        out = """clcache statistics:
  current cache dir        : {}
  cache size               : {:,} bytes
  maximum cache size       : {:,} bytes
  cache entries            : {}
  cache hits               : {}
  cache misses             : {}
  called for linking       : {}
  called w/o sources       : {}
  calls w/ multiple sources: {}
  calls w/ PCH             : {}
  evicted misses           : {}
  header changed misses    : {}
  source changed misses    : {}
  """.format(
         cache.cacheDirectory(),
         stats.currentCacheSize(),
         cfg.maximumCacheSize(),
         stats.numCacheEntries(),
         stats.numCacheHits(),
         stats.numCacheMisses(),
         stats.numCallsForLinking(),
         stats.numCallsWithoutSourceFile(),
         stats.numCallsWithMultipleSourceFiles(),
         stats.numCallsWithPch(),
         stats.numEvictedMisses(),
         stats.numHeaderChangedMisses(),
         stats.numSourceChangedMisses())
    print(out)

def resetStatistics():
    cache = ObjectCache()
    with cache.lock:
        stats = CacheStatistics(cache)
        stats.resetCounters()
        stats.save()
    print 'Statistics reset'


# Returns pair - list of includes and new compiler output.
# Output changes if strip is True in that case all lines with include
# directives are stripped from it
def getIncludes(compilerOutput, sourceFile, baseDir, strip):
    newOutput = []
    includesSet = set([])
    reFilePath = re.compile('^Note: including file: *(?P<file_path>.+)$')
    absSourceFile = os.path.normcase(os.path.abspath(sourceFile))
    if baseDir:
        baseDir = os.path.normcase(baseDir)
    for line in compilerOutput.split('\n'):
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
        return list(includesSet), '\n'.join(newOutput)
    else:
        return list(includesSet), compilerOutput


def addObjectToCache(stats, cache, outputFile, compilerOutput, compilerStderr, cachekey):
    printTraceStatement("Adding file " + outputFile + " to cache using " +
                        "key " + cachekey)
    cache.setEntry(cachekey, outputFile, compilerOutput, compilerStderr)
    if outputFile != '':
        stats.registerCacheEntry(os.path.getsize(outputFile))
        cfg = Configuration(cache)
        cache.clean(stats, cfg.maximumCacheSize())


def processCacheHit(stats, cache, outputFile, cachekey):
    stats.registerCacheHit()
    stats.save()
    printTraceStatement("Reusing cached object for key " + cachekey + " for " +
                        "output file " + outputFile)
    if outputFile != '':
        if os.path.exists(outputFile):
            os.remove(outputFile)
        copyOrLink(cache.cachedObjectName(cachekey), outputFile)
    compilerOutput = cache.cachedCompilerOutput(cachekey)
    compilerStderr = cache.cachedCompilerStderr(cachekey)
    printTraceStatement("Finished. Exit code 0")
    return 0, compilerOutput, compilerStderr


def processObjectEvicted(stats, cache, outputFile, cachekey, compiler, cmdLine):
    stats.registerEvictedMiss()
    printTraceStatement("Cached object already evicted for key " + cachekey + " for " +
                        "output file " + outputFile)
    returnCode, compilerOutput, compilerStderr = invokeRealCompiler(compiler, cmdLine, captureOutput=True)
    if returnCode == 0 and (outputFile == '' or os.path.exists(outputFile)):
        addObjectToCache(stats, cache, outputFile, compilerOutput, compilerStderr, cachekey)
    stats.save()
    printTraceStatement("Finished. Exit code %d" % returnCode)
    return returnCode, compilerOutput, compilerStderr


def processHeaderChangedMiss(stats, cache, outputFile, manifest, manifestHash, keyInManifest, compiler, cmdLine):
    cachekey = cache.getDirectCacheKey(manifestHash, keyInManifest)
    stats.registerHeaderChangedMiss()
    returnCode, compilerOutput, compilerStderr = invokeRealCompiler(compiler, cmdLine, captureOutput=True)
    if returnCode == 0 and (outputFile == '' or os.path.exists(outputFile)):
        addObjectToCache(stats, cache, outputFile, compilerOutput, compilerStderr, cachekey)
        removedItems = []
        while len(manifest.hashes) >= MAX_MANIFEST_HASHES:
            key, objectHash = manifest.hashes.popitem()
            removedItems.append(objectHash)
        cache.removeObjects(stats, removedItems)
        manifest.hashes[keyInManifest] = cachekey
        cache.setManifest(manifestHash, manifest)
    stats.save()
    printTraceStatement("Finished. Exit code %d" % returnCode)
    return returnCode, compilerOutput, compilerStderr


def processNoManifestMiss(stats, cache, outputFile, manifestHash, baseDir, compiler, cmdLine, sourceFile):
    stats.registerSourceChangedMiss()
    stripIncludes = '/showIncludes' not in cmdLine
    returnCode, compilerOutput, compilerStderr = invokeRealCompiler(compiler, cmdLine, captureOutput=True)
    grabStderr = False
    # If these options present, cl.exe will list includes on stderr, not stdout
    for option in ['/E', '/EP', '/P']:
        if option in cmdLine:
            grabStderr = True
            break
    if grabStderr:
        listOfIncludes, compilerStderr = getIncludes(compilerStderr, sourceFile, baseDir, stripIncludes)
    else:
        listOfIncludes, compilerOutput = getIncludes(compilerOutput, sourceFile, baseDir, stripIncludes)
    manifest = Manifest(listOfIncludes, {})
    listOfHeaderHashes = [getRelFileHash(fileName, baseDir) for fileName in listOfIncludes]
    keyInManifest = cache.getKeyInManifest(listOfHeaderHashes)
    cachekey = cache.getDirectCacheKey(manifestHash, keyInManifest)

    if returnCode == 0 and (outputFile == '' or os.path.exists(outputFile)):
        addObjectToCache(stats, cache, outputFile, compilerOutput, compilerStderr, cachekey)
        manifest.hashes[keyInManifest] = cachekey
        cache.setManifest(manifestHash, manifest)
    stats.save()
    printTraceStatement("Finished. Exit code %d" % returnCode)
    return returnCode, compilerOutput, compilerStderr


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--help":
        print """\
    clcache.py v3.0.1
      --help   : show this help
      -s       : print cache statistics
      -z       : reset cache statistics
      -M <size>: set maximum cache size (in bytes)
    """
        return 0

    if len(sys.argv) == 2 and sys.argv[1] == "-s":
        printStatistics()
        return 0

    if len(sys.argv) == 2 and sys.argv[1] == "-z":
        resetStatistics()
        return 0

    if len(sys.argv) == 3 and sys.argv[1] == "-M":
        cache = ObjectCache()
        with cache.lock:
            cfg = Configuration(cache)
            cfg.setMaximumCacheSize(int(sys.argv[2]))
            cfg.save()
        return 0

    compiler = findCompilerBinary()
    if not compiler:
        print "Failed to locate cl.exe on PATH (and CLCACHE_CL is not set), aborting."
        return 1

    printTraceStatement("Found real compiler binary at '%s'" % compiler)

    if "CLCACHE_DISABLE" in os.environ:
        return invokeRealCompiler(compiler, sys.argv[1:])[0]
    try:
        exitCode, compilerStdout, compilerStderr = processCompileRequest(compiler, sys.argv)
        sys.stdout.write(compilerStdout)
        sys.stderr.write(compilerStderr)
        return exitCode
    except LogicException as e:
        print e
        return 1


def processCompileRequest(compiler, args):
    printTraceStatement("Parsing given commandline '%s'" % args[1:])

    cmdLine = expandCommandLine(sys.argv[1:])
    printTraceStatement("Expanded commandline '%s'" % cmdLine)
    analysisResult, sourceFile, outputFile = analyzeCommandLine(cmdLine)

    if analysisResult == AnalysisResult.MultipleSourceFilesSimple:
        return reinvokePerSourceFile(cmdLine, sourceFile), '', ''

    cache = ObjectCache()
    stats = CacheStatistics(cache)
    if analysisResult != AnalysisResult.Ok:
        with cache.lock:
            if analysisResult == AnalysisResult.NoSourceFile:
                printTraceStatement("Cannot cache invocation as %s: no source file found" % (' '.join(cmdLine)))
                stats.registerCallWithoutSourceFile()
            elif analysisResult == AnalysisResult.MultipleSourceFilesComplex:
                printTraceStatement("Cannot cache invocation as %s: multiple source files found" % (' '.join(cmdLine)))
                stats.registerCallWithMultipleSourceFiles()
            elif analysisResult == AnalysisResult.CalledWithPch:
                printTraceStatement("Cannot cache invocation as %s: precompiled headers in use" % (' '.join(cmdLine)))
                stats.registerCallWithPch()
            elif analysisResult == AnalysisResult.CalledForLink:
                printTraceStatement("Cannot cache invocation as %s: called for linking" % (' '.join(cmdLine)))
                stats.registerCallForLinking()
            elif analysisResult == AnalysisResult.ExternalDebugInfo:
                printTraceStatement("Cannot cache invocation as %s: external debug information (/Zi) is not supported" % (' '.join(cmdLine)))
            stats.save()
        return invokeRealCompiler(compiler, args[1:])
    if 'CLCACHE_NODIRECT' in os.environ:
        return processNoDirect(stats, cache, outputFile, compiler, cmdLine)
    manifestHash = cache.getManifestHash(compiler, cmdLine, sourceFile)
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
        keyInManifest = cache.getKeyInManifest(listOfHeaderHashes)
        cachekey = manifest.hashes.get(keyInManifest)
        if cachekey is not None:
            if cache.hasEntry(cachekey):
                return processCacheHit(stats, cache, outputFile, cachekey)
            else:
                return processObjectEvicted(stats, cache, outputFile, cachekey, compiler, cmdLine)
        else:
            # Some of header files changed - recompile and add to manifest
            return processHeaderChangedMiss(stats, cache, outputFile, manifest, manifestHash, keyInManifest, compiler, cmdLine)
    else:
        return processNoManifestMiss(stats, cache, outputFile, manifestHash, baseDir, compiler, cmdLine, sourceFile)


def processNoDirect(stats, cache, outputFile, compiler, cmdLine):
    cachekey = cache.computeKey(compiler, cmdLine)
    if cache.hasEntry(cachekey):
        with cache.lock:
            stats.registerCacheHit()
            stats.save()
        printTraceStatement("Reusing cached object for key " + cachekey + " for " +
                            "output file " + outputFile)
        if os.path.exists(outputFile):
            os.remove(outputFile)
        copyOrLink(cache.cachedObjectName(cachekey), outputFile)
        compilerStdout = cache.cachedCompilerOutput(cachekey)
        compilerStderr = cache.cachedCompilerStderr(cachekey)
        printTraceStatement("Finished. Exit code 0")
        return 0, compilerStdout, compilerStderr
    else:
        returnCode, compilerStdout, compilerStderr = invokeRealCompiler(compiler, cmdLine, captureOutput=True)
        with cache.lock:
            stats.registerCacheMiss()
            if returnCode == 0 and os.path.exists(outputFile):
                printTraceStatement("Adding file " + outputFile + " to cache using " +
                                    "key " + cachekey)
                cache.setEntry(cachekey, outputFile, compilerStdout, compilerStderr)
                stats.registerCacheEntry(os.path.getsize(outputFile))
                cfg = Configuration(cache)
                cache.clean(stats, cfg.maximumCacheSize())
            stats.save()
        printTraceStatement("Finished. Exit code %d" % returnCode)
        return returnCode, compilerStdout, compilerStderr

if __name__ == '__main__':
    sys.exit(main())
