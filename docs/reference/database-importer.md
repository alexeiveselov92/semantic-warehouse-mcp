# The database importer

A database no dbt project describes still has a catalog: its tables and views, their columns and types, and the comments
its owners wrote. `semantic-warehouse-mcp import-database` reads them through a connection of your configuration and
writes a **draft** of an [Apache Ossie](semantic-models.md) document — one dataset per table, one field per column —
for a person, or an agent, to give meaning to.

```bash
semantic-warehouse-mcp import-database --config config.yaml --output semantic/warehouse.yaml
# then, under the connection:  semantic_models: [semantic/warehouse.yaml]
semantic-warehouse-mcp --config config.yaml --check                 # the server checks it against the database
```

A semantic model is never required: without one the server serves every readable table and its columns live
(`list_tables`, `describe_table`). The draft is where the meaning goes that the catalog does not hold — what each table
holds, how its numbers are counted, which columns join (`ai_context`, descriptions, relationships, metrics).

## What it writes

Only what the database says — nothing is guessed:

| The database's | The draft's |
|---|---|
| each table and view `readable` lets agents read | a dataset, its `source` the table; named after the table — `<schema>__<table>` when two schemas hold one name |
| a table's comment | the dataset's `description` |
| each column | a field, its expression the column's name (in double quotes when SQL cannot read it bare) |
| a column's comment | the field's `description` |
| a column whose type holds a date or a time (`Date`, `DateTime64(3)`, `timestamp with time zone`, …) | the field marked a time dimension (`dimension: {is_time: true}`) |

A table the configuration does not let agents read is never drafted — the draft names nothing an agent could not read.
The model's own `description` and `ai_context` are left for you to write; so are primary keys and relationships, which
a catalog rarely holds as such.

## The command

```text
semantic-warehouse-mcp import-database --config <file> --output <file> [--connection NAME] [--name NAME]
                                       [--tables schema.table …] [--fields all|described] [--overwrite]
```

| Option | Default | What |
|---|---|---|
| `--config` | `SEMANTIC_WAREHOUSE_MCP_CONFIG` | the server's [configuration](configuration.md): the connection is read from it — reached as the server reaches it, without its semantic models, context or skills, so the configuration may already name the draft about to be written |
| `--connection` | the only one | the connection to draft; needed when the configuration holds several |
| `--output` | required | the Ossie document written, relative to where the command runs |
| `--name` | the connection's | the model's name |
| `--tables` | every readable table | which tables, as `schema.table` patterns (`*` and `?` within a part); give it again for more. A pattern naming none of the readable tables is refused |
| `--fields` | `all` | `all`: a field for every column, for you to describe; `described`: only the columns the database holds a comment for |
| `--overwrite` | | write over an existing file. Without it an existing file is refused: a draft is meant to be edited, and an edit is never lost |

It exits `2`, naming every problem, when the configuration or the connection is wrong, a pattern is refused, or a name
holds a backslash or a control character (which each database reads its own way — leave that table out with
`--tables`). What it wrote, it says: how many datasets and fields, how many descriptions came from the database's
comments, how many time columns.

## Giving it meaning

The draft is a starting point, edited like any semantic model — by hand or by an agent: through this server an agent
reads each table's columns and a sample of its values (`describe_table`, `run_query`), and writes what the table holds
and how to count it into the draft; `--check` then holds the result to Ossie's schema and to the database. A table
that is wide or rarely asked about may stay as drafted, or be left out of the model: the server still serves it live.
