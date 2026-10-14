"""Private Unix-socket qualification commands, distinct from HA presentation."""
import json
import socket
from uuid import UUID, uuid4

SOCKET = '/run/edsys-bedroom-display/control.sock'
KEYS = {'up':'Up', 'down':'Down', 'left':'Left', 'right':'Right',
        'select':'Select', 'back':'Back', 'home':'FirstPage', 'info':'Info'}


def validate_request(data, scenes):
    if not isinstance(data, dict) or not isinstance(data.get('request_id'),str):
        raise ValueError('schema')
    UUID(data['request_id'])
    op = data.get('op')
    fields = {'request_id','op'}
    if op=='scene':
        fields.add('scene')
        if data.get('scene') not in scenes:
            raise ValueError('scene')
        if 'duration_seconds' in data:
            fields.add('duration_seconds')
            duration=data['duration_seconds']
            if type(duration) is not int or not 1<=duration<=90:raise ValueError('duration')
    elif op=='key':
        fields.add('key')
        if data.get('key') not in KEYS:
            raise ValueError('key')
    elif op not in ('status','dismiss','screenshot','play_test','stop_test','update_repos','install_plex','open_plex'):
        raise ValueError('operation')
    if set(data)!=fields:
        raise ValueError('fields')
    return data


def request(data):
    frame={'request_id':str(uuid4()), **data}
    raw=json.dumps(frame).encode()+b'\n'
    if len(raw)>4096:
        raise ValueError('request too large')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(SOCKET)
        client.sendall(raw)
        result=bytearray()
        while b'\n' not in result:
            part=client.recv(4096)
            if not part or len(result)+len(part)>32768:
                raise ValueError('incomplete or oversized response; outcome uncertain')
            result.extend(part)
    return json.loads(result)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Local qualification control; never automatically retries.')
    parser.add_argument('op',choices=['status','scene','dismiss','screenshot','key','play_test','stop_test','update_repos','install_plex','open_plex'])
    parser.add_argument('value',nargs='?')
    parser.add_argument('--duration-seconds',type=int)
    a=parser.parse_args()
    data={'op':a.op}
    if a.op=='scene':data['scene']=a.value
    elif a.op=='key':data['key']=a.value
    elif a.value is not None:parser.error('Unexpected argument')
    if a.duration_seconds is not None:
        if a.op!='scene':parser.error('Duration applies only to a synthetic scene')
        data['duration_seconds']=a.duration_seconds
    print(json.dumps(request(data),indent=2))
