# Wikipedia Reading Lists MCP Server

An MCP server that lets Claude read and manage a user's Wikipedia reading lists
(saved articles), for example turning a conversation about a topic into a
reading list saved in the user's Wikipedia account.

## Stack and layout

- Python, managed with uv. Dependencies live in `pyproject.toml`; exact
  versions are pinned in `uv.lock`.
- MCP Python SDK (`mcp[cli]`).
- Code lives in `src/wikipedia_reading_lists_mcp/`:
  - `server.py`: the MCP server and its tools/resources
- Design decisions are recorded as ADRs in `docs/decisions/`.

## Commands

- Install/sync dependencies: `uv sync`
- Add a dependency: `uv add <package>`
- Test in the MCP Inspector: `uv run mcp dev src/wikipedia_reading_lists_mcp/server.py`
- Run the server directly (stdio): `uv run python src/wikipedia_reading_lists_mcp/server.py`

## Conventions

- The server uses the stdio transport, so stdout carries the protocol.
  Never print to stdout; send logs and debug output to stderr.
- Credentials (Wikipedia login, tokens) go in a `.env` file or environment
  variables and must never be committed. `.env` is listed in `.gitignore`.
- Tool docstrings are what Claude reads to decide when to use a tool. Write
  them as clear API docs: what the tool does, when to use it, and what each
  parameter means.
- Commit messages follow Conventional Commits.