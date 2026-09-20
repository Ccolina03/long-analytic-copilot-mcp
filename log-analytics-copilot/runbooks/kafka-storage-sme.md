# Runbook: Kafka Storage (directory entry, no agent deployed)

**Agent id:** `kafka-storage`
**Domain:** Log subsystem — segments, retention, compaction, tiered storage.
**Upstream:** Apache Kafka, `storage/` module.
**Implemented SME agent:** no. Discovery still evaluates this team. A ticket
whose impact signals include `log_storage` will be recorded as "must review,
no agent deployed" rather than silently skipped.

```
OWNS = [
    "storage/src/main/java/org/apache/kafka/storage/",
]
```

## Architecture

The log subsystem owns bytes on disk: segment files, indexes, retention,
compaction, and (where enabled) tiered storage to remote objects. A partition
replica is a `UnifiedLog` of segments. This team does not own request
dispatch (`kafka-broker`) or group metadata (`group-coordinator`).
`__consumer_offsets` is a compacted topic whose *record format* is owned
by `group-coordinator`; the *log* it is stored in is owned here.

## CODEOWNERS

```
storage/src/main/java/org/apache/kafka/storage/
├── internals/log/UnifiedLog.java
├── internals/log/LogSegment.java
└── internals/checkpoint/
```

Consult this team when a change would alter segment layout, retention,
compaction keys, or remote-storage metadata. Do not consult it because a
request happens to read a topic.
