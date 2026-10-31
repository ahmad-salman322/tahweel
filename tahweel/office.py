"""Print Word documents to PDF with LibreOffice (headless), using a private profile so an open
LibreOffice window neither blocks nor receives the job."""

import os
import shutil
import subprocess
import tempfile

PROFILE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "tahweel", "lo-profile")


def soffice():
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    return None


def to_pdf(src, dest, timeout=300):
    """Convert an office document (.docx, .odt, .doc …) to PDF at `dest`."""
    exe = soffice()
    if exe is None:
        raise RuntimeError("no-libreoffice")
    os.makedirs(PROFILE, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tahweel-") as tmp:
        # copy under a plain name: LibreOffice names the output after the input
        work = os.path.join(tmp, "in" + os.path.splitext(src)[1].lower())
        shutil.copyfile(src, work)
        cmd = [exe, f"-env:UserInstallation=file://{PROFILE}", "--headless", "--norestore", "--nologo",
               "--convert-to", "pdf", "--outdir", tmp, work]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL)
        out = os.path.join(tmp, "in.pdf")
        if not os.path.exists(out):
            msg = (res.stderr or res.stdout or "").strip().splitlines()
            raise RuntimeError("LibreOffice: " + (msg[-1] if msg else f"exit code {res.returncode}"))
        shutil.move(out, dest)
