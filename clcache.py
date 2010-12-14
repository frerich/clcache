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

    def appropriateForCaching(self):
        foundCompileOnlySwitch = False
        foundSourceFile = False
        for arg in self.cmdline[1:]:
            if arg == "/link":
                return False
            if arg == "/c":
                foundCompileOnlySwitch = True
            if arg[0] != '/':
                if foundSourceFile == True:
                    return False
                foundSourceFile = True
        return foundSourceFile and foundCompileOnlySwitch

    def outputFileName(self):
        srcFile = ""
        for arg in self.cmdline:
            if arg[:3] == "/Fo":
                return arg[3:]
            if arg[0] != '/':
                srcFile = os.path.basename(arg)
        return os.path.join(os.getcwd(), os.path.splitext(srcFile)[0] + ".obj")

class ObjectCache:
    def __init__(self):
        try:
            self.dir = os.environ["CLCACHE_DIR"]
        except KeyError:
            self.dir = os.path.join(os.path.expanduser("~"), "clcache")
        if not os.path.exists(self.dir):
            os.mkdir(self.dir)

    def computeKey(self, commandLine):
        compilerBinary = commandLine[0]

        ppcmd = list(commandLine)
        ppcmd.remove("/c")
        ppcmd.append("/EP")
        preprocessedSourceCode = subprocess.Popen(ppcmd,
                                                  stdout=subprocess.PIPE,
                                                  stderr=open(os.devnull, 'w')).communicate()[0]
        normalizedCmdLine = self.__normalizedCommandLine(commandLine[1:])

        import hashlib
        sha = hashlib.sha1()
        sha.update(str(long(os.path.getmtime(compilerBinary))))
        sha.update(str(os.path.getsize(compilerBinary)))
        sha.update(' '.join(normalizedCmdLine))
        sha.update(preprocessedSourceCode)
        return sha.hexdigest()

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

    def __normalizedCommandLine(self, cmdline):
        def isRelevantArgument(arg):
            for preprocessorArg in [ "/AI", "/C", "/E", "/P", "/FI", "/u", "/X",
                                     "/FU", "/D", "/EP", "/Fx", "/U", "/I" ]:
                if arg[:len(preprocessorArg)] == preprocessorArg:
                    return False
            return True
        return filter(isRelevantArgument, cmdline)


def printTraceStatement(msg):
    if "CLCACHE_LOG" in os.environ:
        print "*** clcache.py: " + msg

compiler = findCompilerBinary()
cmdline = CommandLine(sys.argv)
realCmdline = [compiler] + sys.argv[1:]

if "CLCACHE_DISABLE" in os.environ:
    sys.exit(subprocess.call(realCmdline))

if not cmdline.appropriateForCaching():
    printTraceStatement("Command line " + ' '.join(realCmdline) + " is not appropriate for caching, forwarding to real compiler.")
    sys.exit(subprocess.call(realCmdline))

cache = ObjectCache()
cachekey = cache.computeKey(realCmdline)
if cache.hasEntry(cachekey):
    printTraceStatement("Reusing cached object for key " + cachekey + " for output file " + cmdline.outputFileName())
    import shutil
    shutil.copyfile(cache.cachedObjectName(cachekey),
                    cmdline.outputFileName())
    sys.stdout.write(cache.cachedCompilerOutput(cachekey))
    sys.exit(0)

printTraceStatement("Invoking real compiler as " + ' '.join(realCmdline))
compilerProcess = subprocess.Popen(realCmdline, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
compilerOutput = compilerProcess.communicate()[0]

returnCode = compilerProcess.returncode
printTraceStatement("Real compiler finished with exit code " + str(returnCode) + ", object should be in " + cmdline.outputFileName())

if returnCode == 0:
    printTraceStatement("Adding file " + cmdline.outputFileName() + " to cache using key " + cachekey)
    cache.setEntry(cachekey, cmdline.outputFileName(), compilerOutput)

sys.stdout.write(compilerOutput)
sys.exit(returnCode)


