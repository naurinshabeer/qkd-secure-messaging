"""Bounded length-prefixed TCP application protocol."""
from __future__ import annotations
import socket, struct
from dataclasses import dataclass
VERSION=1
TYPE_MESSAGE, TYPE_ACK, TYPE_NACK, TYPE_OUTCOME_QUERY, TYPE_OUTCOME = 1, 2, 3, 4, 5
MAX_PAYLOAD=16_384
MAX_KEY_ID=64
MAX_MESSAGE_ID=64
MAX_SESSION_ID=64
MAX_FRAME=6+MAX_KEY_ID+MAX_MESSAGE_ID+MAX_SESSION_ID+10+12+4+MAX_PAYLOAD
_PREFIX=struct.Struct("!I")
_META=struct.Struct("!BBHHHQ")
_LENGTH=struct.Struct("!I")
class ProtocolError(ValueError): pass
@dataclass(frozen=True)
class Frame:
    message_type:int
    key_id:str
    nonce:bytes
    payload:bytes
    message_id:str="message"
    sequence:int=0
    session_id:str="session"
def encode_frame(frame:Frame)->bytes:
    try: key=frame.key_id.encode("ascii"); mid=frame.message_id.encode("ascii"); sid=frame.session_id.encode("ascii")
    except UnicodeEncodeError as exc: raise ProtocolError("IDs must be ASCII.") from exc
    if not 1<=len(key)<=MAX_KEY_ID or not 1<=len(mid)<=MAX_MESSAGE_ID or not 1<=len(sid)<=MAX_SESSION_ID: raise ProtocolError("Invalid ID length.")
    if len(frame.nonce)!=12: raise ProtocolError("Nonce must be 12 bytes.")
    if len(frame.payload)>MAX_PAYLOAD: raise ProtocolError("Payload exceeds maximum size.")
    if frame.message_type not in (TYPE_MESSAGE,TYPE_ACK,TYPE_NACK,TYPE_OUTCOME_QUERY,TYPE_OUTCOME): raise ProtocolError("Unknown message type.")
    body=_META.pack(VERSION,frame.message_type,len(key),len(mid),len(sid),frame.sequence)+key+mid+sid+frame.nonce+_LENGTH.pack(len(frame.payload))+frame.payload
    return _PREFIX.pack(len(body))+body
def recv_exact(sock:socket.socket,size:int)->bytes:
    chunks=bytearray()
    while len(chunks)<size:
        block=sock.recv(size-len(chunks))
        if not block: raise ProtocolError("Truncated TCP frame.")
        chunks.extend(block)
    return bytes(chunks)
def recv_frame(sock:socket.socket)->Frame:
    size=_PREFIX.unpack(recv_exact(sock,4))[0]
    if size<_META.size+3+12+4 or size>MAX_FRAME: raise ProtocolError("Invalid or oversized TCP frame.")
    version,typ,klen,mlen,slen,seq=_META.unpack(recv_exact(sock,_META.size))
    if version!=VERSION or typ not in (TYPE_MESSAGE,TYPE_ACK,TYPE_NACK,TYPE_OUTCOME_QUERY,TYPE_OUTCOME) or not 1<=klen<=MAX_KEY_ID or not 1<=mlen<=MAX_MESSAGE_ID or not 1<=slen<=MAX_SESSION_ID: raise ProtocolError("Invalid frame metadata.")
    try: key=recv_exact(sock,klen).decode("ascii"); mid=recv_exact(sock,mlen).decode("ascii"); sid=recv_exact(sock,slen).decode("ascii")
    except UnicodeDecodeError as exc: raise ProtocolError("IDs must be ASCII.") from exc
    nonce=recv_exact(sock,12); n=_LENGTH.unpack(recv_exact(sock,4))[0]
    if n>MAX_PAYLOAD or _META.size+klen+mlen+slen+12+4+n!=size: raise ProtocolError("Invalid payload length.")
    return Frame(typ,key,nonce,recv_exact(sock,n),mid,seq,sid)
def send_frame(sock:socket.socket,frame:Frame)->None: sock.sendall(encode_frame(frame))
