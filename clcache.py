#!/usr/bin/env python

import os
import subprocess
import sys

def findCompilerBinary():
    try:
        path = os.environ["CLCACHE_CL"]
        if os.path.exists(path):
            return path
    except KeyError:
        for dir in os.environ["PATH"].split(os.pathsep):
            path = os.path.join(dir, "cl.exe")
            if os.path.exists(path):
                return path
    return None

class CommandLine:
    def __init__(self, args):
        self.cmdline = args

    def calledForLink(self):
        return (not "/c" in self.cmdline) or ("/link" in self.cmdline)

    def outputFileName(self):
        srcFile = ""
        for arg in self.cmdline:
            if arg[:3] == "/Fo":
                return arg[3:]
            if arg[0] != '/':
                srcFile = os.path.basename(arg)
        return os.path.join(os.getcwd(), os.path.splitext(srcFile)[0] + ".obj")

class ObjectCache:
    def __init__(self, dir):
        self.dir = dir
        if not os.path.exists(self.dir):
            os.mkdir(self.dir)

    def hasEntry(self, key):
        return os.path.exists(self.cachedObjectName(key))

    def setEntry(self, key, objectFileName, compilerOutput):
        if not os.path.exists(self.__cacheEntryDir(key)):
            os.makedirs(self.__cacheEntryDir(key))
        import shutil
        shutil.copyfile(objectFileName, self.cachedObjectName(key))
        open(self.__cachedCompilerOutputName(key), 'w').write(compilerOutput)

    def cachedObjectName(self, key):
        return os.path.join(self.__cacheEntryDir(key), "object")

    def cachedCompilerOutput(self, key):
        return open(self.__cachedCompilerOutputName(key), 'r').read()

    def __cacheEntryDir(self, key):
        return os.path.join(self.dir, key[:2], key)

    def __cachedCompilerOutputName(self, key):
        return os.path.join(self.__cacheEntryDir(key), "output.txt")


def getPreprocessedOutput(cmdline):
    cmd = list(cmdline)
    cmd.remove("/c")
    cmd.append("/EP")

    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=open(os.devnull, 'w')).communicate()[0]

def generateHash(compiler, cmdline, ppoutput):
    import hashlib
    import time
    sha = hashlib.sha1()
    sha.update(str(long(os.path.getmtime(compiler))))
    sha.update(str(os.path.getsize(compiler)))
    sha.update(' '.join(cmdline))
    sha.update(ppoutput)
    return sha.hexdigest()

def printTraceStatement(msg):
    if "CLCACHE_LOG" in os.environ:
        print "*** clcache.py: " + msg

compiler = findCompilerBinary()
cmdline = CommandLine(sys.argv)
realCmdline = [compiler] + sys.argv[1:]

if "CLCACHE_DISABLE" in os.environ:
    sys.exit(subprocess.call(realCmdline))

if cmdline.calledForLink():
    printTraceStatement("Command line " + ' '.join(realCmdline) + " called for linking, forwarding...")
    sys.exit(subprocess.call(realCmdline))

ppoutput = getPreprocessedOutput(realCmdline)

try:
    cachedir = os.environ["CLCACHE_DIR"]
except KeyError:
    cachedir = os.path.join(os.path.expanduser("~"), "clcache")

cache = ObjectCache(cachedir)
cachekey = generateHash(compiler, realCmdline, ppoutput)
if cache.hasEntry(cachekey):
    printTraceStatement("Reusing cached object for key " + cachekey + " for output file " + cmdline.outputFileName())
    import shutil
    shutil.copyfile(cache.cachedObjectName(cachekey),
                    cmdline.outputFileName())
    print(cache.cachedCompilerOutput(cachekey))
    sys.exit(0)

printTraceStatement("Invoking real compiler as " + ' '.join(realCmdline))
compilerProcess = subprocess.Popen(realCmdline, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
compilerOutput = compilerProcess.communicate()[0]

returnCode = compilerProcess.returncode
printTraceStatement("Real compiler finished with exit code " + str(returnCode) + ", object should be in " + cmdline.outputFileName())

if returnCode == 0:
    printTraceStatement("Adding file " + cmdline.outputFileName() + " to cache using key " + cachekey)
    cache.setEntry(cachekey, cmdline.outputFileName(), compilerOutput)

print(compilerOutput)
sys.exit(returnCode)


