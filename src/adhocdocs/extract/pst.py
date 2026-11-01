"""Outlook data files (.pst, .ost), read with libpff.

A PST is a container, like a zip or an mbox: each message becomes a child
document, an email made by :func:`mapi.to_eml` from its MAPI properties.
A child's ``part`` is its folder path and its position in the folder
(``Inbox/Vendors/3``), leaving out the root and the "Top of Personal
Folders" level. Folders' calendar items, contacts and tasks come through
as emails too, with their subject and body. Deleted items libpff can
recover (orphans) are counted, not ingested. A message attached to a
message is read through libpff itself, since pypff has no call for it (see
:func:`_attached_message`).
"""

import io
import sys

from . import Child, Extraction, Layer
from . import mapi

MAX_MESSAGES = 1_000_000


def _props(record_set) -> tuple[dict, dict]:
    props, raw8 = {}, {}
    for e in record_set.entries:
        try:
            pid, ptype = e.entry_type, e.value_type
            data = e.data
        except (OSError, IOError, ValueError):
            continue
        if data is None or ptype & 0x1000:
            continue
        if ptype == mapi.PT_STRING8:
            raw8[pid] = data
        elif ptype == mapi.PT_OBJECT:
            continue
        else:
            props[pid] = mapi.decode(ptype, data)
    return props, raw8


def _record_sets(item) -> list:
    try:
        return list(item.record_sets)
    except (OSError, IOError):
        return []


def read_message(item, depth: int = 0) -> mapi.Message:
    sets = _record_sets(item)
    props, raw8 = _props(sets[0]) if sets else ({}, {})
    cp = mapi.codepage(props)
    mapi.finish_strings(props, raw8, cp)
    msg = mapi.Message(props)
    try:
        recipients = item.recipients
    except (OSError, IOError, AttributeError):
        recipients = None
    if recipients is not None:
        for rs in _record_sets(recipients):
            rp, r8 = _props(rs)
            mapi.finish_strings(rp, r8, cp)
            msg.recipients.append(rp)
    try:
        attachments = list(item.attachments)
    except (OSError, IOError, AttributeError):
        attachments = []
    for a in attachments:
        asets = _record_sets(a)
        ap, a8 = _props(asets[0]) if asets else ({}, {})
        mapi.finish_strings(ap, a8, cp)
        ap.pop(mapi.ATTACH_DATA, None)
        att = mapi.Attachment(ap)
        method = ap.get(mapi.ATTACH_METHOD)
        if method == mapi.ATTACH_EMBEDDED_MSG:
            inner = _attached_message(a) if depth < 20 else None
            if inner is not None:
                att.message = read_message(inner, depth + 1)
            else:
                att.unread = True
        elif method in (None, mapi.ATTACH_BY_VALUE, 6):
            try:
                size = a.get_size()
                att.data = a.read_buffer(size) if size else b""
            except (OSError, IOError):
                att.data = None
        msg.attachments.append(att)
    return msg


_bridge = None


def _attached_message(attachment):
    """The message attached to an attachment, as a ``pypff.message``.

    pypff has no call for this, though libpff does
    (``libpff_attachment_get_item``), and the module exports it along with
    pypff's own constructor. So call those through ctypes: the libpff item
    is the first field after the Python object header. None where the
    module doesn't export them (it does on Linux and macOS)."""
    global _bridge
    import ctypes
    import pypff
    if _bridge is None:
        try:
            lib = ctypes.PyDLL(pypff.__file__)
            get_item = lib.libpff_attachment_get_item
            get_item.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)]
            get_item.restype = ctypes.c_int
            free_error = lib.libpff_error_free
            free_error.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
            new = lib.pypff_item_new
            new.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.py_object]
            new.restype = ctypes.py_object
            _bridge = (get_item, free_error, new)
        except (OSError, AttributeError):
            _bridge = False
    if not _bridge or not isinstance(attachment, pypff.attachment):
        return None
    get_item, free_error, new = _bridge
    item = ctypes.c_void_p.from_address(id(attachment) + object.__basicsize__).value
    if not item:
        return None
    out, err = ctypes.c_void_p(), ctypes.c_void_p()
    if get_item(item, ctypes.byref(out), ctypes.byref(err)) != 1 or not out.value:
        if err.value:
            free_error(ctypes.byref(err))
        return None
    return new(id(pypff.message), out.value, attachment)


def _walk(folder, path: list[str], out: list, top: bool):
    try:
        name = folder.name
    except (OSError, IOError):
        name = None
    # The root has no name; "Top of Personal Folders" (or of the
    # Information Store, or of Outlook data file) is the level below it.
    here = path if (top or not name or (len(path) == 0 and name.lower().startswith("top of "))) else path + [name]
    try:
        messages = list(folder.sub_messages)
    except (OSError, IOError):
        messages = []
    for i, item in enumerate(messages, 1):
        out.append(("/".join(here), i, item))
        if len(out) >= MAX_MESSAGES:
            return
    try:
        subs = list(folder.sub_folders)
    except (OSError, IOError):
        subs = []
    for sub in subs:
        _walk(sub, here, out, False)


def extract_pst(data: bytes) -> Extraction:
    import pypff
    f = pypff.file()
    f.open_file_object(io.BytesIO(data))
    try:
        found: list = []
        _walk(f.root_folder, [], found, True)
        children, failed, unread = [], 0, 0
        folders = set()
        for folder, i, item in found:
            folders.add(folder)
            source = "Outlook PST" + (f", folder {folder}" if folder else "")
            try:
                msg = read_message(item)
                data = mapi.to_eml(msg, source)
            except Exception as e:      # one bad message shouldn't lose the rest of the mailbox
                failed += 1
                print(f"warning: PST message {folder}/{i} unreadable: {e}", file=sys.stderr)
                continue
            subject = msg.props.get(mapi.SUBJECT) or f"message-{i}"
            unread += sum(a.unread for a in msg.attachments)
            children.append(Child(part=f"{folder}/{i}" if folder else str(i), name=_filename(subject), data=data))
        try:
            orphans = f.number_of_orphan_items
        except (OSError, IOError):
            orphans = 0
    finally:
        f.close()
    meta = {"members": len(children), "folders": sorted(folders), "unreadable": failed,
            "unread_attached_messages": unread, "orphans": orphans}
    return Extraction(kind="mailbox", layers=[Layer("native", "", primary=True)],
                      metadata={k: v for k, v in meta.items() if v}, children=children)


def _filename(subject: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_.,()&'" else "_" for c in subject).strip(" .")
    return (keep[:120] or "message") + ".eml"
