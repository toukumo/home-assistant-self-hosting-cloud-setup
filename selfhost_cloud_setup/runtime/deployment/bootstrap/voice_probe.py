#!/usr/bin/env python3
"""Check a specified local Wyoming service without recording or synthesizing audio.

A successful Describe response proves protocol reachability and advertised
capability only. It does not prove end-to-end recognition or synthesis.
"""
import argparse
import asyncio
import ipaddress
import json
import re
from urllib.parse import urlsplit

MAX_HEADER=4096
MAX_DATA=65536
LOCAL_NETWORKS=tuple(ipaddress.ip_network(x) for x in (
    '10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','127.0.0.0/8','fc00::/7','::1/128'))


def endpoint(value):
    if not isinstance(value,str) or len(value)>128:raise ValueError('Expected tcp://IP:port')
    try:
        uri=urlsplit(value)
        if uri.scheme!='tcp' or not uri.hostname or uri.username or uri.password or uri.path or uri.query or uri.fragment or not uri.port:
            raise ValueError('Expected tcp://IP:port')
        if '%' in uri.hostname:raise ValueError('Scoped addresses are not supported')
        addr=ipaddress.ip_address(uri.hostname)
        if not any(addr in network for network in LOCAL_NETWORKS if network.version==addr.version):
            raise ValueError('Use a private LAN or loopback IP address')
        # Parsing and connecting use the same literal IP; no DNS lookup/rebinding.
        if not re.fullmatch(r'tcp://(?:\[[0-9a-fA-F:]+\]|[0-9.]+):[0-9]{1,5}',value):
            raise ValueError('Expected tcp://IP:port')
        return str(addr),uri.port
    except (ValueError,TypeError):raise ValueError('Use tcp:// followed by a private LAN IP and port') from None


def object_pairs(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError('Duplicate JSON field')
        out[key]=value
    return out


def obj(data):
    value=json.loads(data,object_pairs_hook=object_pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid constant')))
    if not isinstance(value,dict):raise ValueError('Expected object')
    return value


def capabilities(data, kind):
    programs=data.get('asr' if kind=='stt' else 'tts',[])
    if not isinstance(programs,list) or len(programs)>128:raise ValueError('Invalid service metadata')
    if not programs:return 'wrong_service'
    candidates=[]
    for program in programs:
        if not isinstance(program,dict):raise ValueError('Invalid service metadata')
        installed=program.get('installed')
        assets=program.get('models' if kind=='stt' else 'voices',[])
        if type(installed) is not bool or not isinstance(assets,list) or len(assets)>512:
            raise ValueError('Invalid service metadata')
        for asset in assets:
            if not isinstance(asset,dict) or type(asset.get('installed')) is not bool:
                raise ValueError('Invalid service metadata')
            candidates.append(installed and asset['installed'])
    return 'reachable' if any(candidates) else 'not_installed'


async def probe(value,kind,timeout=5):
    host,port=endpoint(value)
    if kind not in ('stt','tts'):raise ValueError('Expected stt or tts')
    writer=None
    try:
        async with asyncio.timeout(timeout):
            reader,writer=await asyncio.open_connection(host,port,limit=MAX_HEADER)
            writer.write(b'{"type":"describe"}\n');await writer.drain()
            header=await reader.readline()
            if not header or len(header)>MAX_HEADER:raise ValueError('Invalid frame header')
            event=obj(header)
            if event.get('type')!='info':raise ValueError('Unexpected event')
            size=event.get('data_length',0);payload=event.get('payload_length',0)
            if type(size) is not int or not 0<=size<=MAX_DATA or type(payload) is not int or payload!=0:
                raise ValueError('Invalid frame size')
            data=event.get('data',{})
            if not isinstance(data,dict):raise ValueError('Invalid event data')
            if size:
                extra=obj(await reader.readexactly(size))
                if data.keys() & extra.keys():raise ValueError('Duplicate event data')
                data={**data,**extra}
            result=capabilities(data,kind)
            return {'state':result,'kind':kind,'functional_audio_tested':False}
    except (OSError,TimeoutError,asyncio.TimeoutError):
        return {'state':'unreachable','kind':kind,'functional_audio_tested':False}
    except (ValueError,UnicodeError,RecursionError,asyncio.IncompleteReadError):
        return {'state':'invalid_response','kind':kind,'functional_audio_tested':False}
    finally:
        if writer is not None:
            writer.close()
            try:await asyncio.wait_for(writer.wait_closed(),1)
            except (OSError,TimeoutError):pass


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--endpoint',required=True);p.add_argument('--kind',choices=('stt','tts'),required=True);a=p.parse_args()
    try:result=asyncio.run(probe(a.endpoint,a.kind))
    except ValueError as e:p.exit(1,f'Invalid input: {e}\n')
    print(json.dumps(result,indent=2));p.exit(0 if result['state']=='reachable' else 2)
if __name__=='__main__':main()
