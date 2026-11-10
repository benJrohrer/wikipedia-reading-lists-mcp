# Wikipedia Reading Lists MCP Server

An MCP server that lets Claude read and manage a user's Wikipedia reading lists
(saved articles), for example turning a conversation about a topic into a
reading list saved in the user's Wikipedia account.

## Stack and layout

- Python, managed with uv. Dependencies live in `pyproject.toml`; exact
  versions are pinned in `uv.lock`.
- MCP Python SDK (`mcp[cli]`).
- Code lives in `src/wikipedia_reading_lists_mcp/`, in two layers. Nothing in
  the API layer imports `mcp` or knows tool names.
  - MCP layer:
    - `server.py`: the MCP server and its tools/resources
    - `tool_errors.py`: error hints and the decorator that turns
      `WikimediaAPIError` into `ToolError`
  - API layer:
    - `wikimedia.py`: Wikimedia API calls (one function per operation)
    - `http_client.py`: the HTTP client, auth, error parsing, and rate-limit
      retries. Call its verb helpers through the module (`http_client.get`).
    - `errors.py`: `WikimediaAPIError` and its subclasses
    - `models.py`: TypedDicts for tool inputs and results
- `server.py` and `wikimedia.py` also run as scripts, so imports within the
  package are absolute. Don't name a module after a stdlib one (`http.py`,
  `types.py`): the script's folder comes first on `sys.path` and would shadow it.
- Design decisions are recorded as ADRs in `docs/decisions/`.
- Project status, next steps, and a dated progress log live in `docs/ROADMAP.md`.
  Individual work items are GitHub issues; the roadmap links to them rather
  than repeating their details.

## Commands

- Install/sync dependencies: `uv sync`
- Add a dependency: `uv add <package>`
- Test in the MCP Inspector: `uv run mcp dev src/wikipedia_reading_lists_mcp/server.py`
- Run the server directly (stdio): `uv run python src/wikipedia_reading_lists_mcp/server.py`
- Test with an environment override (e.g. a bad token). `mcp dev` can't pass
  environment variables, and the Inspector filters the server's environment, so
  call the Inspector directly. Add `--cli --method tools/call --tool-name <name>`
  after `inspector` to call a tool from the terminal:
  `npx @modelcontextprotocol/inspector -e WIKIMEDIA_ACCESS_TOKEN=notarealtoken uv run --directory "$PWD" python src/wikipedia_reading_lists_mcp/server.py`
- Pass tool arguments in the Inspector's CLI mode with `--tool-arg key=value`,
  and put `--` before the server command, because `--tool-arg` otherwise takes
  it as another argument. Values are parsed as JSON, so
  `--tool-arg 'description=""'` sends an empty string, which the Inspector's
  form can't do:
  `npx @modelcontextprotocol/inspector --cli --method tools/call --tool-name update_list --tool-arg list_id=<id> --tool-arg 'description=""' -- uv run --directory "$PWD" python src/wikipedia_reading_lists_mcp/server.py`

## Conventions

- The server uses the stdio transport, so stdout carries the protocol.
  Never print to stdout; send logs and debug output to stderr.
- Credentials (Wikipedia login, tokens) go in a `.env` file or environment
  variables and must never be committed. `.env` is listed in `.gitignore`.
- Tool docstrings are what Claude reads to decide when to use a tool. Write
  them as clear API docs: what the tool does, when to use it, and what each
  parameter means.
- Commit messages follow Conventional Commits.