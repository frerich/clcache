from clcache import CacheFileStrategy, getStringHash, printTraceStatement, CompilerArtifacts, \
    CACHE_COMPILER_OUTPUT_STORAGE_CODEC

from pymemcache.client.base import Client
from pymemcache.serde import (python_memcache_serializer,
                              python_memcache_deserializer)


class CacheDummyLock(object):
    def __enter__(self):
        pass

    def __exit__(self, typ, value, traceback):
        pass


class CacheMemcacheStrategy(object):
    def __init__(self, server, cacheDirectory=None, manifestPrefix='manifests_', objectPrefix='objects_'):
        self.fileStrategy = CacheFileStrategy(cacheDirectory=cacheDirectory)
        # XX Memcache Strategy should be independent

        self.lock = CacheDummyLock()
        self.local_cache = {}
        self.local_manifest = {}
        self.objectPrefix = objectPrefix
        self.manifestPrefix = manifestPrefix

        self.connect(server)

    def connect(self, server):
        server = CacheMemcacheStrategy.splitHosts(server)
        assert len(server) > 0, "%s is a suitable server" % server
        if len(server) == 1:
            client_class = Client
            server = server[0]
        else:
            from pymemcache.client.hash import HashClient
            client_class = HashClient
            server = server
        self.client = client_class(server, ignore_exc=True,
                                   serializer=python_memcache_serializer,
                                   deserializer=python_memcache_deserializer,
                                   timeout=5,
                                   connect_timeout=5,
                                   key_prefix=(getStringHash(self.fileStrategy.dir) + "_").encode("UTF-8")
                                   )
        # XX key_prefix ties fileStrategy cache to memcache entry
        # because tests currently the integration tests use this to start with clean cache
        # Prevents from having cache hits in when code base is in different locations
        # adding code to production just for testing purposes

    def server(self):
        return self.client.server

    @staticmethod
    def splitHost(host):
        port = 11211
        index = host.rfind(':')
        if index != -1:
            host, port = host[:index], int(host[index + 1:])
        if not host or port > 65535:
            raise ValueError
        return host.strip(), port

    @staticmethod
    def splitHosts(hosts):
        """
        :param hosts: A string in the format of HOST:PORT[,HOST:PORT]
        :return: a list [(HOST, int(PORT)), ..] of tuples that can be consumed by socket.connect()
        """
        return [CacheMemcacheStrategy.splitHost(h) for h in hosts.split(',')]

    def __str__(self):
        return "Remote Memcache @{} object-prefix: {}".format(self.server, self.objectPrefix)

    @property
    def statistics(self):
        return self.fileStrategy.statistics

    @property
    def configuration(self):
        return self.fileStrategy.configuration

    @staticmethod
    def lockFor(key):
        return CacheDummyLock()

    @staticmethod
    def manifestLockFor(key):
        return CacheDummyLock()

    def _fetchEntry(self, key):
        data = self.client.get((self.objectPrefix + key).encode("UTF-8"))
        if data is not None:
            self.local_cache[key] = data
            return True
        self.local_cache[key] = None
        return None

    def hasEntry(self, key):
        return (key in self.local_cache and self.local_cache[key] is not None) or self._fetchEntry(key) is not None

    def getEntry(self, key):
        if key not in self.local_cache:
            self._fetchEntry(key)
        if self.local_cache[key] is None:
            return None
        data = self.local_cache[key]

        printTraceStatement("{} remote cache hit for {} dumping into local cache".format(self, key))

        assert len(data) == 3

        # XX this is writing the remote objectfile into the local cache
        # because the current cache lookup assumes that getEntry gives us an Entry in local cache
        # so it can copy it to the build destination later

        with self.fileStrategy.lockFor(key):
            objectFilePath = self.fileStrategy.deserializeCacheEntry(key, data[0])

        return CompilerArtifacts(objectFilePath,
                                 data[1].decode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC),
                                 data[2].decode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC)
                                 )

    def setEntry(self, key, artifacts):
        assert artifacts.objectFilePath
        with open(artifacts.objectFilePath, 'rb') as objectFile:
            self.set_ignore_exc(self.objectPrefix + key,
                                [objectFile.read(),
                                 artifacts.stdout.encode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC),
                                 artifacts.stderr.encode(CACHE_COMPILER_OUTPUT_STORAGE_CODEC)],
                                )

    def setManifest(self, manifestHash, manifest):
        self.set_ignore_exc(self.manifestPrefix + manifestHash, manifest)

    def set_ignore_exc(self, key, value):
        try:
            self.client.set(key.encode("UTF-8"), value)
        except Exception:
            self.client.close()
            if self.client.ignore_exc:
                printTraceStatement("Could not set {} in memcache {}".format(key, self.server()))
                return None
            raise

    def getManifest(self, manifestHash):
        return self.client.get((self.manifestPrefix + manifestHash).encode("UTF-8"))

    def clean(self, stats, maximumSize):
        self.fileStrategy.clean(stats,
                                maximumSize)
