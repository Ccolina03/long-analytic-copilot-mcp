# Runbook: Kafka Streams (directory entry, no agent deployed)

**Agent id:** `kafka-streams`
**Domain:** Kafka Streams runtime, topologies, internal topics.
**Upstream:** Apache Kafka, `streams/` module.
**Implemented SME agent:** no.

```
OWNS = [
    "streams/src/main/java/org/apache/kafka/streams/",
]
```

## Architecture

Streams is a client-side runtime. It builds a topology, creates internal
changelog and repartition topics, and consumes/produces like any other
application. Group membership for a Streams app is a consumer group like
any other — `group-coordinator` owns that. This team owns the runtime,
DSL/Processor API, state stores, and how internal topics are named and
configured.

## CODEOWNERS

```
streams/src/main/java/org/apache/kafka/streams/
├── KafkaStreams.java
├── Topology.java
├── processor/
└── state/
```

Consult this team when a change would affect Streams topologies, state
stores, or internal-topic contracts. A broker or coordinator change that
Streams merely calls through AdminClient or the consumer is not enough.
