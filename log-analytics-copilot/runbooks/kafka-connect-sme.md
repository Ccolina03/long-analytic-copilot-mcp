# Runbook: Kafka Connect Runtime (directory entry, no agent deployed)

**Agent id:** `kafka-connect`
**Domain:** Connect framework — workers, connector rebalancing, REST API.
**Upstream:** Apache Kafka, `connect/runtime/`.
**Implemented SME agent:** no.

MirrorMaker 2 *runs on* Connect but is not this team. MM2 connectors live
in `connect/mirror/` and are owned by `mirrormaker`. This team owns the
worker that hosts them.

```
OWNS = [
    "connect/runtime/src/main/java/org/apache/kafka/connect/runtime/",
]
```

## Architecture

A Connect cluster is a set of workers that share a config topic, an offset
topic, and a status topic. The runtime assigns connector tasks to workers,
rebalances on membership change, and exposes a REST API. Connectors
(including MM2) are plugins the runtime loads; their behaviour is not
this team's.

## CODEOWNERS

```
connect/runtime/src/main/java/org/apache/kafka/connect/runtime/
├── Worker.java
├── distributed/DistributedHerder.java
└── rest/
```

Consult this team when a change would affect worker lifecycle, connector
rebalancing, the REST API, or the Connect internal topics. A change inside
a specific connector is the connector team's.
