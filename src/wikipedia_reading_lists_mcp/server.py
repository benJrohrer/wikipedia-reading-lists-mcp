from mcp.server import MCPServer

mcp = MCPServer("Demo")

@mcp.tool()
def add(a: int, b:int) -> int:
    """Add two numbers."""
    return a + b

@mcp.tool()
def mult(a: int, b:int) -> int:
    """multiply two numbers."""
    return a * b

@mcp.resource("greeting://{name}")
def greeting(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"

if __name__ == "__main__":
    mcp.run()