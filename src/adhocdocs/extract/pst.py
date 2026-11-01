"""Outlook data files (.pst, .ost), read with libpff.

A PST is a container, like a zip or an mbox: each message becomes a child
document, an email made by :func:`mapi.to_eml` from its MAPI properties.
A child's ``part`` is its folder path and its position in the folder
(``Inbox/Vendors/3``), leaving out the root and the "Top of Personal
Folders" level. Folders' calendar items, contacts and tasks come through
as emails too, with their subject and body. Deleted items libpff can
recover (orphans) are counted, not ingested. A PST on disk is read in
place, a message at a time (:func:`extract_pst_file`), so it needn't fit
in memory. A message attached to a
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


def _count(folder, attr: str) -> int:
    try:
        return getattr(folder, attr)
    except (OSError, IOError):
        return 0


def _walk(folder, path: list[str], top: bool):
    """Each message as ``(folder path, position, item)``, a folder's own
    messages before its subfolders'. Items are fetched one at a time, so a
    folder of any size costs nothing to walk."""
    try:
        name = folder.name
    except (OSError, IOError):
        name = None
    # The root has no name; "Top of Personal Folders" (or of the
    # Information Store, or of Outlook data file) is the level below it.
    here = path if (top or not name or (len(path) == 0 and name.lower().startswith("top of "))) else path + [name]
    for i in range(_count(folder, "number_of_sub_messages")):
        try:
            item = folder.get_sub_message(i)
        except (OSError, IOError):
            item = None
        yield "/".join(here), i + 1, item
    for j in range(_count(folder, "number_of_sub_folders")):
        try:
            sub = folder.get_sub_folder(j)
        except (OSError, IOError):
            continue
        yield from _walk(sub, here, False)


def extract_pst(data: bytes) -> Extraction:
    """A PST in memory (an attachment, a zip member), read in full."""
    import pypff
    f = pypff.file()
    f.open_file_object(io.BytesIO(data))
    ext = _extraction(f)
    ext.children = list(ext.children)
    return ext


def extract_pst_file(path) -> Extraction:
    """A PST on disk, of any size: libpff reads it as needed, and its
    messages are converted one at a time as ``children`` is iterated.
    ``metadata`` is complete once the iteration ends."""
    import pypff
    f = pypff.file()
    f.open(str(path))
    return _extraction(f)


def _extraction(f) -> Extraction:
    meta: dict = {}
    return Extraction(kind="mailbox", layers=[Layer("native", "", primary=True)],
                      metadata=meta, children=_children(f, meta))


def _children(f, meta: dict):
    members, failed, unread = 0, 0, 0
    folders = set()
    try:
        for folder, i, item in _walk(f.root_folder, [], True):
            folders.add(folder)
            source = "Outlook PST" + (f", folder {folder}" if folder else "")
            try:
                if item is None:
                    raise OSError("libpff could not open it")
                msg = read_message(item)
                data = mapi.to_eml(msg, source)
            except Exception as e:      # one bad message shouldn't lose the rest of the mailbox
                failed += 1
                print(f"warning: PST message {folder}/{i} unreadable: {e}", file=sys.stderr)
                continue
            subject = msg.props.get(mapi.SUBJECT) or f"message-{i}"
            unread += sum(a.unread for a in msg.attachments)
            members += 1
            yield Child(part=f"{folder}/{i}" if folder else str(i), name=_filename(subject), data=data)
            if members + failed >= MAX_MESSAGES:
                break
    finally:
        try:
            orphans = f.number_of_orphan_items
        except (OSError, IOError):
            orphans = 0
        f.close()
        found = {"members": members, "folders": sorted(folders), "unreadable": failed,
                 "unread_attached_messages": unread, "orphans": orphans}
        meta.update({k: v for k, v in found.items() if v})


def _filename(subject: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_.,()&'" else "_" for c in subject).strip(" .")
    return (keep[:120] or "message") + ".eml"
