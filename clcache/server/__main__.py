# We often don't use all members of all the pyuv callbacks
# pylint: disable=unused-argument
import hashlib
import logging
import os
import pickle
import signal
import argparse
import re

import pyuv

class HashCache:
    def __init__(self, loop, excludePatterns, disableWatching):
        self._loop = loop
        self._watchedDirectories = {}
        self._handlers = []
        self._excludePatterns = excludePatterns or []
        self._disableWatching = disableWatching

    def getFileHash(self, path):
        logging.debug("getting hash for %s", path)
        dirname, basename = os.path.split(os.path.normcase(path))

        watchedDirectory = self._watchedDirectories.get(dirname, {})
        hashsum = watchedDirectory.get(basename)
        if hashsum:
            logging.debug("using cached hashsum %s", hashsum)
            return hashsum

        with open(path, 'rb') as f:
            hashsum = hashlib.md5(f.read()).hexdigest()

        watchedDirectory[basename] = hashsum
        if dirname not in self._watchedDirectories and not self.isExcluded(dirname) and not self._disableWatching:
            logging.debug("starting to watch directory %s for changes", dirname)
            self._startWatching(dirname)

        self._watchedDirectories[dirname] = watchedDirectory

        logging.debug("calculated and stored hashsum %s", hashsum)
        return hashsum

    def _startWatching(self, dirname):
        ev = pyuv.fs.FSEvent(self._loop)
        ev.start(dirname, 0, self._onPathChange)
        self._handlers.append(ev)

    def _onPathChange(self, handle, filename, events, error):
        watchedDirectory = self._watchedDirectories[handle.path]
        logging.debug("detected modifications in %s", handle.path)
        if filename in watchedDirectory:
            logging.debug("invalidating cached hashsum for %s", os.path.join(handle.path, filename))
            del watchedDirectory[filename]

    def __del__(self):
        for ev in self._handlers:
            ev.stop()

    def isExcluded(self, dirname):
        # as long as we do not have more than _MAXCACHE regex we can
        # rely on the internal cacheing of re.match
        excluded = any(re.search(pattern, dirname, re.IGNORECASE) for pattern in self._excludePatterns)
        if excluded:
            logging.debug("NOT watching %s", dirname)
        return excluded


class Connection:
    def __init__(self, pipe, cache, onCloseCallback):
        self._readBuffer = b''
        self._pipe = pipe
        self._cache = cache
        self._onCloseCallback = onCloseCallback
        pipe.start_read(self._onClientRead)

    def _onClientRead(self, pipe, data, error):
        self._readBuffer += data
        if self._readBuffer.endswith(b'\x00'):
            paths = self._readBuffer[:-1].decode('utf-8').splitlines()
            logging.debug("received request to hash %d paths", len(paths))
            try:
                hashes = map(self._cache.getFileHash, paths)
                response = '\n'.join(hashes).encode('utf-8')
            except OSError as e:
                response = b'!' + pickle.dumps(e)
            pipe.write(response + b'\x00', self._onWriteDone)

    def _onWriteDone(self, pipe, error):
        logging.debug("sent response to client, closing connection")
        self._pipe.close()
        self._onCloseCallback(self)


class PipeServer:
    def __init__(self, loop, address, cache):
        self._pipeServer = pyuv.Pipe(loop)
        self._pipeServer.bind(address)
        self._connections = []
        self._cache = cache

    def listen(self):
        self._pipeServer.listen(self._onConnection)

    def _onConnection(self, pipe, error):
        logging.debug("detected incoming connection")
        client = pyuv.Pipe(self._pipeServer.loop)
        pipe.accept(client)
        self._connections.append(Connection(client, self._cache, self._connections.remove))


def closeHandlers(handle):
    for h in handle.loop.handles:
        h.close()


def onSigint(handle, signum):
    logging.info("Ctrl+C detected, shutting down")
    closeHandlers(handle)


def onSigterm(handle, signum):
    logging.info("Server was killed by SIGTERM")
    closeHandlers(handle)


def main():
    logging.basicConfig(format='%(asctime)s [%(levelname)s]: %(message)s', level=logging.INFO)

    parser = argparse.ArgumentParser(description='Server process for clcache to cache hash values of headers \
                                                  and observe them for changes.')
    parser.add_argument('--exclude', metavar='REGEX', action='append', \
                        help='Regex ( re.search() ) for exluding of directory watching. Can be specified \
                              multiple times. Example: --exclude \\\\build\\\\')
    parser.add_argument('--disable_watching', action='store_true', help='Disable watching of directories which \
                         we have in the cache.')
    args = parser.parse_args()

    for pattern in args.exclude or []:
        logging.info("Not watching paths which match: %s", pattern)

    if args.disable_watching:
        logging.info("Disabled directory watching")

    eventLoop = pyuv.Loop.default_loop()

    cache = HashCache(eventLoop, vars(args)['exclude'], args.disable_watching)

    server = PipeServer(eventLoop, r'\\.\pipe\clcache_srv', cache)
    server.listen()

    signalHandle = pyuv.Signal(eventLoop)
    signalHandle.start(onSigint, signal.SIGINT)
    signalHandle.start(onSigterm, signal.SIGTERM)

    logging.info("clcachesrv started")
    eventLoop.run()


if __name__ == '__main__':
    main()
