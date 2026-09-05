"""Wave 20 (FILE-01): the ticket's attachments, classified from their bytes.

The rehearsal's triage called the customer's logo "PNG, inte vektoriserad"
without ever opening it — the customer had said "png", and the model repeated
it. Here every File attached to the ticket, or to one of its messages (which
is where a mail attachment lands), is read and given a deterministic verdict:
a PNG is raster whatever its name says, a PDF whose page draws paths is
vector, a PDF whose page draws one image is raster. The verdict is stored
per file on `HD Ticket File` and does not change on a re-run.
"""

from __future__ import annotations

import io
import re

import frappe

from helpdesk.utils import agent_only

QUEUE = "short"
JOB_PREFIX = "helpdesk-ticket-files"

RASTER_FORMATS = {"png", "jpg", "gif", "tif", "bmp", "webp"}
DOCUMENT_SUFFIXES = {"doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf", "odt", "ods", "csv"}
VECTOR_SUFFIXES = {"ai", "eps", "svg", "cdr"}

# Path-painting operators of the PDF content stream syntax, as whole tokens
# (S stroke, s close+stroke, f/F/f* fill, B/B*/b/b* fill+stroke). `n` ends a
# path without painting — clipping — and is deliberately not one of them.
_PAINT = re.compile(rb"(?:^|(?<=\s))(?:S|s|f\*?|F|B\*?|b\*?)(?=\s|$)")
_TEXT = re.compile(rb"(?:^|(?<=\s))(?:Tj|TJ|'|\")(?=\s|$)")
_DRAW = re.compile(rb"(?:^|(?<=\s))/([^\s/\[\]<>()]+)\s+Do(?=\s|$)")
# Inline image data (BI ... ID <bytes> EI) may contain any byte and must not be
# mistaken for operators.
_INLINE_IMAGE = re.compile(rb"(?:^|(?<=\s))BI\s.*?\sEI(?=\s|$)", re.S)


# ---------------------------------------------------------------------------
# Classification of bytes
# ---------------------------------------------------------------------------


def _suffix(name: str) -> str:
    return (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""


def _magic_format(data: bytes, suffix: str) -> str:
    """The format the first bytes say, falling back to the name's suffix."""
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "tif"
    if head.startswith(b"BM"):
        return "bmp"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"%PDF"):
        # An .ai saved with PDF compatibility is a PDF by bytes; the suffix is
        # what tells prepress it opens in Illustrator, so it survives.
        return "ai" if suffix == "ai" else "pdf"
    if head.startswith(b"%!PS"):
        return "eps" if b"EPSF" in data[:256] else "ps"
    lead = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    if lead.startswith(b"<svg") or (lead.startswith(b"<?xml") and b"<svg" in data[:4096]):
        return "svg"
    return suffix


def _pdf_verdict(data: bytes) -> dict:
    """Vektor, Raster or Dokument from what the pages actually draw.

    A page that paints paths is vector, unless it is mostly text with a few
    rules — an order confirmation with table lines is a document, not a logo.
    A page that draws only image XObjects is a raster in a PDF wrapper.
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    paints = texts = images = 0

    def scan(stream: bytes, resources) -> None:
        nonlocal paints, texts, images
        stream = _INLINE_IMAGE.sub(b" ", stream)
        images += stream.count(b"BI ")
        paints += len(_PAINT.findall(stream))
        texts += len(_TEXT.findall(stream))
        xobjects = {}
        try:
            xobjects = (resources or {}).get("/XObject") or {}
            xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
        except Exception:
            xobjects = {}
        for name in set(_DRAW.findall(stream)):
            try:
                xobject = xobjects["/" + name.decode("latin-1")].get_object()
            except Exception:
                continue
            subtype = str(xobject.get("/Subtype", ""))
            if subtype == "/Image":
                images += 1
            elif subtype == "/Form":
                scan(xobject.get_data(), xobject.get("/Resources"))

    for page in reader.pages:
        try:
            contents = page.get_contents()
            stream = contents.get_data() if contents is not None else b""
        except Exception:
            stream = b""
        scan(stream, page.get("/Resources"))

    if paints and paints >= texts:
        kind, vector = "Vektor", 1
    elif texts and texts > paints:
        kind, vector = "Dokument", 0
    elif images:
        kind, vector = "Raster", 0
    elif paints:
        kind, vector = "Vektor", 1
    else:
        kind, vector = "Dokument", 0
    return {"kind": kind, "vector": vector, "pages": pages}


def _raster_size(data: bytes) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:
        return None, None


def classify_bytes(name: str, data: bytes) -> dict:
    """The deterministic verdict on one file: format, kind, vector, sizes.

    Magic bytes first, the name's suffix only where the bytes are mute. The
    same bytes always give the same answer, so a refresh never changes a row.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    data = data or b""
    suffix = _suffix(name)
    fmt = _magic_format(data, suffix)
    verdict = {"format": fmt, "kind": "Övrigt", "vector": 0,
               "width": None, "height": None, "pages": None}

    if fmt in RASTER_FORMATS:
        verdict["kind"] = "Raster"
        verdict["width"], verdict["height"] = _raster_size(data)
    elif fmt in ("pdf", "ai"):
        try:
            verdict.update(_pdf_verdict(data))
        except Exception:
            # Unreadable PDF: still a PDF, still an original by suffix, but
            # the bytes could not be judged and a guess would be a lie.
            verdict["kind"] = "Vektor" if fmt == "ai" else "Dokument"
            verdict["vector"] = 1 if fmt == "ai" else 0
    elif fmt in ("eps", "ps", "svg") or fmt in VECTOR_SUFFIXES:
        verdict["kind"] = "Vektor"
        verdict["vector"] = 1
    elif fmt in DOCUMENT_SUFFIXES:
        verdict["kind"] = "Dokument"

    verdict["relevance"] = _relevance(verdict)
    return verdict


def _relevance(verdict: dict) -> str:
    """What the desk counts: originals (images, vector or raster) and PDF
    documents as order material. Everything else — a docx, a calendar
    invite, an unknown blob — is Övrigt and not counted."""
    if verdict["kind"] in ("Vektor", "Raster"):
        return "Original"
    if verdict["kind"] == "Dokument" and verdict["format"] in ("pdf", "ai"):
        return "Underlag"
    return "Övrigt"


# ---------------------------------------------------------------------------
# The inventory
# ---------------------------------------------------------------------------


def _attached_files(ticket_id: str) -> list[dict]:
    """Every distinct attachment on the ticket or on one of its Communications.

    A reply with an attachment gives Helpdesk two File rows for one upload —
    one on the Communication, one on the HD Ticket, same file_url (see
    HD Ticket.attach_file_with_doc). The inventory wants the attachment, not
    the bookkeeping: rows sharing a file_url (or, lacking one, a content
    hash) collapse to one, the HD Ticket-attached row preferred, the oldest
    otherwise.
    """
    fields = ["name", "file_name", "file_url", "content_hash", "attached_to_doctype",
              "attached_to_name", "creation"]
    rows = frappe.get_all(
        "File",
        filters={"attached_to_doctype": "HD Ticket", "attached_to_name": ticket_id,
                 "is_folder": 0},
        fields=fields,
    )
    messages = frappe.get_all(
        "Communication",
        filters={"reference_doctype": "HD Ticket", "reference_name": ticket_id},
        pluck="name",
    )
    if messages:
        rows += frappe.get_all(
            "File",
            filters={"attached_to_doctype": "Communication",
                     "attached_to_name": ["in", messages], "is_folder": 0},
            fields=fields,
        )
    return _dedupe(rows)


def _dedupe(rows: list[dict]) -> list[dict]:
    def key(file: dict) -> str:
        return file.file_url or file.content_hash or file.name

    def rank(file: dict) -> tuple:
        return (0 if file.attached_to_doctype == "HD Ticket" else 1, file.creation, file.name)

    chosen: dict[str, dict] = {}
    for file in sorted(rows, key=rank):
        chosen.setdefault(key(file), file)
    return list(chosen.values())


def _content(file_name: str) -> bytes:
    doc = frappe.get_doc("File", file_name)
    data = doc.get_content()
    if isinstance(data, str):
        data = data.encode("utf-8")
    return data or b""


def _sync(ticket_id: str, reclassify: bool) -> None:
    """Upsert one inventory row per attached File; prune rows whose File is gone.

    Keyed on the File, so a second run finds its rows rather than adding
    more. With `reclassify` every row is read again from its bytes; without
    it only files not yet in the inventory are opened.

    The GET endpoint and the queued worker both sync, possibly at once, so
    the ticket's row is locked first: whoever asks first writes, the other
    waits and then finds the rows. `ticket_file` is unique as a last guard,
    and a duplicate insert is answered by reading the row that won.
    """
    frappe.db.get_value("HD Ticket", ticket_id, "name", for_update=True)
    files = _attached_files(ticket_id)
    existing = {
        row.file: row
        for row in frappe.get_all(
            "HD Ticket File", filters={"ticket": ticket_id}, fields=["name", "file"]
        )
    }
    changed = False
    seen = set()
    for file in files:
        seen.add(file.name)
        row = existing.get(file.name)
        if row and not reclassify:
            continue
        changed = True
        try:
            verdict = classify_bytes(file.file_name, _content(file.name))
        except Exception:
            frappe.log_error(
                title="Helpdesk ticket files",
                message=f"could not read {file.name} on {ticket_id}\n\n{frappe.get_traceback()}",
            )
            verdict = classify_bytes(file.file_name, b"")
        values = {
            "file_name": file.file_name,
            "file_url": file.file_url,
            "source": "message" if file.attached_to_doctype == "Communication" else "ticket",
            "message": file.attached_to_name if file.attached_to_doctype == "Communication" else None,
            **verdict,
        }
        if not row:
            try:
                frappe.get_doc({"doctype": "HD Ticket File", "ticket": ticket_id,
                                "file": file.name, "ticket_file": f"{ticket_id}:{file.name}",
                                **values}).insert(ignore_permissions=True)
                continue
            except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
                # Someone else inserted this row between our read and our
                # write; theirs stands, ours becomes an update of it.
                name = frappe.db.get_value(
                    "HD Ticket File", {"ticket": ticket_id, "file": file.name}, "name"
                )
                if not name:
                    raise
                row = frappe._dict(name=name)
        frappe.db.set_value("HD Ticket File", row.name, values, update_modified=True)
    # Rows whose File is gone, and rows for a File that another File now
    # stands in for (the Communication copy of a ticket attachment), leave.
    for file_name, row in existing.items():
        if file_name not in seen:
            frappe.delete_doc("HD Ticket File", row.name, ignore_permissions=True, force=True)
            changed = True
    if changed:
        # `ticket_files` is read over GET; a page whose files the worker has
        # not reached yet still gets its rows kept, not rolled back.
        frappe.local.flags.commit = True


ROW_FIELDS = [
    "name", "file", "file_name", "file_url", "format", "kind", "vector", "width",
    "height", "pages", "relevance", "source", "message", "assessment", "assessed_by",
]


def _rows(ticket_id: str) -> list[dict]:
    return frappe.get_all(
        "HD Ticket File", filters={"ticket": ticket_id}, fields=ROW_FIELDS,
        order_by="creation asc",
    )


@frappe.whitelist()
@agent_only
def ticket_files(ticket_id: str) -> list[dict]:
    """The inventory: one row per file on the ticket, from its bytes.

    Files the queued classification has not reached yet are read here, so the
    strip never shows fewer files than the ticket has; rows already judged
    are returned as they stand.
    """
    if not frappe.db.exists("HD Ticket", ticket_id):
        frappe.throw(frappe._("Ticket {0} does not exist").format(ticket_id), frappe.DoesNotExistError)
    _sync(ticket_id, reclassify=False)
    return _rows(ticket_id)


@frappe.whitelist(methods=["POST"])
@agent_only
def refresh(ticket_id: str) -> list[dict]:
    """Read every file on the ticket again. Same bytes, same verdict, same rows."""
    if not frappe.db.exists("HD Ticket", ticket_id):
        frappe.throw(frappe._("Ticket {0} does not exist").format(ticket_id), frappe.DoesNotExistError)
    _sync(ticket_id, reclassify=True)
    return _rows(ticket_id)


def refresh_job(ticket_id: str) -> None:
    """Worker entry: the ticket may be gone by the time the job runs."""
    if not frappe.db.exists("HD Ticket", ticket_id):
        return
    _sync(ticket_id, reclassify=True)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def ticket_of_file(doc) -> str | None:
    """The ticket a File belongs to, directly or through its Communication."""
    if doc.attached_to_doctype == "HD Ticket":
        return doc.attached_to_name
    if doc.attached_to_doctype == "Communication" and doc.attached_to_name:
        reference = frappe.db.get_value(
            "Communication", doc.attached_to_name, ["reference_doctype", "reference_name"], as_dict=True
        )
        if reference and reference.reference_doctype == "HD Ticket":
            return reference.reference_name
    return None


def enqueue_for_file(doc, method=None) -> None:
    """Hook target (File.after_insert): queue a classification of the ticket.

    Queued, deduplicated per ticket, and silent on failure: the caller is the
    mail intake writing an attachment, and a lost verdict must never cost the
    attachment itself.
    """
    ticket_id = ticket_of_file(doc)
    if not ticket_id:
        return
    try:
        frappe.enqueue(
            "helpdesk.api.ticket_files.refresh_job",
            queue=QUEUE,
            job_id=f"{JOB_PREFIX}-{ticket_id}",
            deduplicate=True,
            ticket_id=ticket_id,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(
            title="Helpdesk ticket files",
            message=f"could not queue classification for {ticket_id}\n\n{frappe.get_traceback()}",
        )


def on_file_trash(doc, method=None) -> None:
    """Hook target (File.on_trash): a deleted file leaves the inventory.

    Also what lets the File be deleted at all — the inventory links it."""
    for name in frappe.get_all("HD Ticket File", filters={"file": doc.name}, pluck="name"):
        frappe.delete_doc("HD Ticket File", name, ignore_permissions=True, force=True)


def on_ticket_trash(doc, method=None) -> None:
    """Hook target (HD Ticket.on_trash): the inventory goes with the ticket."""
    for name in frappe.get_all("HD Ticket File", filters={"ticket": doc.name}, pluck="name"):
        frappe.delete_doc("HD Ticket File", name, ignore_permissions=True, force=True)
