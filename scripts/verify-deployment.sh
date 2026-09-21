#!/usr/bin/env bash
set -euo pipefail
ROOT=/opt/myfitnesspal-mcp
cd "$ROOT/app"
compose=(docker compose --env-file "$ROOT/config/deployment.env" -f deploy/compose.yaml)
"${compose[@]}" ps
"${compose[@]}" exec -T app python - <<'PY'
import asyncio, os, pathlib
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def verify():
    headers = {'X-MFP-Gateway': pathlib.Path(os.environ['MFP_GATEWAY_TOKEN_FILE']).read_text().strip()}
    async with httpx.AsyncClient(headers=headers, trust_env=False) as client:
        async with streamable_http_client('http://app:8484/mcp', http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert names == {'fitness_get_day', 'fitness_connection_status', 'fitness_sync_today'}, names
                response = await session.call_tool('fitness_connection_status', {})
                assert not response.isError
    async with httpx.AsyncClient(trust_env=False) as client:
        assert (await client.get('http://app:8484/api/status')).status_code == 403
    print('Backend health, gateway isolation, MCP handshake and archive tools verified.')
asyncio.run(verify())
PY
echo 'Also verify the LAN URL from Home Assistant: the internal test cannot prove its network route or source IP.'
