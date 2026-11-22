"""Outlook .msg files (MS-OXMSG): a message saved from Outlook, as a
compound file of MAPI properties. Read into :class:`mapi.Message` and
extracted as the email :func:`mapi.to_eml` makes of it, so a .msg is an
``email`` like any other; its attachments become child documents.
"""

import io
import struct

from . import Extraction
from . import mapi

SUBSTG = "__substg1.0_"
PROPS = "__properties_version1.0"
RECIP = "__recip_version1.0_"
ATTACH = "__attach_version1.0_"
VARIABLE = {mapi.PT_STRING8, mapi.PT_UNICODE, mapi.PT_BINARY, mapi.PT_OBJECT, mapi.PT_CLSID}


def _read_props(ole, path: list[str], header: int) -> tuple[dict, dict, dict]:
    """(props, raw 8-bit strings, embedded storages) of the storage at path.
    header: the size of the properties stream's header (32 for the top
    message, 24 for an embedded one, 8 for recipients and attachments)."""
    props, raw8, objects = {}, {}, {}
    entries = ole.listdir(streams=True, storages=True)
    depth = len(path)
    for e in entries:
        if len(e) != depth + 1 or e[:depth] != path:
            continue
        name = e[-1]
        if not name.startswith(SUBSTG) or len(name) != len(SUBSTG) + 8:
            continue
        try:
            pid, ptype = int(name[-8:-4], 16), int(name[-4:], 16)
        except ValueError:
            continue
        if ptype == mapi.PT_OBJECT:
            objects[pid] = e
            continue
        if ptype & 0x1000:            # multi-valued: not used for email
            continue
        data = ole.openstream(e).read()
        if ptype == mapi.PT_STRING8:
            raw8[pid] = data
        else:
            props[pid] = mapi.decode(ptype, data)
    if path + [PROPS] in [list(x) for x in entries]:
        data = ole.openstream(path + [PROPS]).read()
        for off in range(header, len(data) - 15, 16):
            tag, _flags = struct.unpack_from("<II", data, off)
            ptype, pid = tag & 0xFFFF, tag >> 16
            if ptype in VARIABLE or ptype & 0x1000 or pid in props:
                continue
            props[pid] = mapi.decode(ptype, data[off + 8:off + 16])
    return props, raw8, objects


def _children(ole, path: list[str], prefix: str) -> list[list[str]]:
    depth = len(path)
    found = {tuple(e[:depth + 1]) for e in ole.listdir(streams=True, storages=True)
             if len(e) > depth and e[:depth] == path and e[depth].startswith(prefix)}
    return [list(f) for f in sorted(found)]


def read_message(ole, path: list[str] | None = None, depth: int = 0) -> mapi.Message:
    path = path or []
    props, raw8, _ = _read_props(ole, path, 32 if not path else 24)
    cp = mapi.codepage(props)
    mapi.finish_strings(props, raw8, cp)
    msg = mapi.Message(props)
    for rpath in _children(ole, path, RECIP):
        rp, r8, _ = _read_props(ole, rpath, 8)
        mapi.finish_strings(rp, r8, cp)
        msg.recipients.append(rp)
    for apath in _children(ole, path, ATTACH):
        ap, a8, objects = _read_props(ole, apath, 8)
        mapi.finish_strings(ap, a8, cp)
        att = mapi.Attachment(ap)
        data = ap.pop(mapi.ATTACH_DATA, None)
        if ap.get(mapi.ATTACH_METHOD) == mapi.ATTACH_EMBEDDED_MSG and mapi.ATTACH_DATA in objects and depth < 20:
            att.message = read_message(ole, objects[mapi.ATTACH_DATA], depth + 1)
        elif isinstance(data, bytes):
            att.data = data
        msg.attachments.append(att)
    return msg


def extract_msg(data: bytes) -> Extraction:
    import olefile
    from .email import extract_email
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        msg = read_message(ole)
    ext = extract_email(mapi.to_eml(msg, "Outlook .msg"))
    ext.metadata["format"] = "msg"
    cls = msg.props.get(mapi.MESSAGE_CLASS)
    if cls and cls != "IPM.Note":
        ext.metadata["message_class"] = cls
    return ext
