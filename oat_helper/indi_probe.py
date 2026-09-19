#!/usr/bin/env python3
import socket, time, xml.etree.ElementTree as ET
HOST='127.0.0.1'; PORT=7624; DEVICE='LX200 OpenAstroTech'
VECTOR_TAGS=('defTextVector','setTextVector','defNumberVector','setNumberVector','defSwitchVector','setSwitchVector','defLightVector','setLightVector')

def extract(buf):
    starts=[]
    for tag in VECTOR_TAGS:
        i=buf.find('<'+tag)
        if i>=0: starts.append((i,tag))
    if not starts: return None, buf[-8192:]
    i,tag=min(starts)
    buf=buf[i:]
    close=f'</{tag}>'
    j=buf.find(close)
    if j<0: return None,buf
    j+=len(close)
    return buf[:j],buf[j:]

s=socket.create_connection((HOST,PORT),timeout=3)
s.settimeout(.5)
s.sendall(b'<getProperties version="1.7"/>\n<enableBLOB>Never</enableBLOB>\n')
buf=''; end=time.time()+5
seen=[]
while time.time()<end:
    try:
        d=s.recv(65536)
        if not d: break
        buf+=d.decode('utf-8','replace')
    except socket.timeout:
        pass
    while True:
        item,buf=extract(buf)
        if not item: break
        try: e=ET.fromstring(item)
        except ET.ParseError: continue
        if e.attrib.get('device')!=DEVICE: continue
        name=e.attrib.get('name','')
        children=[c.attrib.get('name','') for c in list(e)]
        key=(e.tag,name,tuple(children))
        if key not in seen:
            seen.append(key)
            print(f'{e.tag:16} vector={name!r} elements={children}')
print('\nExpected useful vectors: Meade, POLAR_ALT, POLAR_AZ')
