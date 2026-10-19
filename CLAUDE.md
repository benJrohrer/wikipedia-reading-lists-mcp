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
  - `wikimedia.py`: the HTTP client and Wikimedia API calls
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

## Conventions

- The server uses the stdio transport, so stdout carries the protocol.
  Never print to stdout; send logs and debug output to stderr.
- Credentials (Wikipedia login, tokens) go in a `.env` file or environment
  variables and must never be committed. `.env` is listed in `.gitignore`.
- Tool docstrings are what Claude reads to decide when to use a tool. Write
  them as clear API docs: what the tool does, when to use it, and what each
  parameter means.
- Commit messages follow Conventional Commits.

## End of day: update the roadmap

When the user says they're wrapping up for the day, update `docs/ROADMAP.md`
before the session ends:

- Rewrite **Current status**, **Next up**, and **Later** so they reflect the
  project as it stands now.
- Add a **Progress log** entry for today at the top of the log (newest first).
  Record what was decided, built, or learned, not every step. Link ADRs and
  issues. Take dates from the git log and issue history, not from memory.
- Update the "Last updated" line.
- The user reviews the changes before committing. Suggested commit:
  `docs: update roadmap for YYYY-MM-DD`.