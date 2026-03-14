import traceback, asyncio
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
m = McpToolset(connection_params=StreamableHTTPConnectionParams(url='http://localhost:8080/sse'))
async def run(): print(await m.get_tools())
try: asyncio.run(run())
except Exception as e: traceback.print_exc()
