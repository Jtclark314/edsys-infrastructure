import asyncio,base64,json
from pathlib import Path
from mcp import ClientSession,StdioServerParameters,stdio_client
async def main():
 async with stdio_client(StdioServerParameters(command='/srv/edsys/edsys-infrastructure/services/local-coder/desktop-mcp.sh')) as (read,write):
  async with ClientSession(read,write) as session:
   await session.initialize()
   tools=await session.list_tools();print('tools',len(tools.tools))
   result=await session.call_tool('desktop_open',{'application':'scratchpad'});assert not result.is_error
   await asyncio.sleep(1)
   screenshot=await session.call_tool('desktop_screenshot',{});assert not screenshot.is_error
   images=[x for x in screenshot.content if x.type=='image'];assert len(images)==1
   Path('/mnt/ai-store/local-coder/evidence/desktop-initial.png').write_bytes(base64.b64decode(images[0].data))
   bad=await session.call_tool('desktop_click',{'x':-1,'y':0});assert bad.is_error
   key=await session.call_tool('desktop_key',{'key':'Tab'});assert not key.is_error
   badkey=await session.call_tool('desktop_key',{'key':'Return;whoami'});assert badkey.is_error
   print(json.dumps({'desktop_tools':len(tools.tools),'screenshot':True,'keyboard':True,'bounds_rejected':True,'invalid_key_rejected':True}))
asyncio.run(main())
