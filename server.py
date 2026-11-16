import os
from mcp.server.fastmcp import FastMCP

VERSION = "0.1.1"
mcp = FastMCP("beira", host="127.0.0.1", port=int(os.getenv("BEIRA_PORT", "8100")))

@mcp.tool()
def ping() -> str:
    """Comprueba que la embajada Beira responde."""
    return f"Beira {VERSION} operativa"

if __name__ == "__main__":
    print(f"Beira {VERSION} arrancando", flush=True)
    mcp.run(transport="streamable-http")
