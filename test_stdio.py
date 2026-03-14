import traceback, asyncio
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
m = McpToolset(connection_params=StdioConnectionParams(command='python', args=['mcp/main.py']))
async def run(): print(await m.get_tools())
try: asyncio.run(run())
except Exception as e: traceback.print_exc()
