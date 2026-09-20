# Runbook: Kafka Tools (directory entry, no agent deployed)

**Agent id:** `kafka-tools`
**Domain:** Shipped command-line tools (`kafka-consumer-groups.sh` and friends).
**Upstream:** Apache Kafka, `tools/` module.
**Implemented SME agent:** no.

```
OWNS = [
    "tools/src/main/java/org/apache/kafka/tools/",
]
```

## Architecture

`tools/` is a set of CLIs over AdminClient. They do not define protocol or
broker behaviour; they call public APIs this org already owns. A new Admin
method may want a CLI flag. A broker-internal change with no Admin surface
does not.

## CODEOWNERS

```
tools/src/main/java/org/apache/kafka/tools/
├── consumer/ConsumerGroupCommand.java
├── TopicCommand.java
└── GetOffsetShell.java
```

Consult this team when a public Admin API change should be exposed on the
CLI. Do not consult it for protocol or broker-internal work.
