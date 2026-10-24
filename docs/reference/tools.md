# Tools

Every tool semantic-warehouse-mcp offers to an agent. All are read-only: each says so to the client (`read_only_hint`, never
`destructive_hint`). A tool appears here in the same commit that adds it — a test fails otherwise.

## `about`

What this server is, its version and status. Call it to check the connection. Takes nothing; answers its name, version,
status and `read_only: true`.
