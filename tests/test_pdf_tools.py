"""Compression and authenticated decryption preserve PDF structure and originals."""

import threading
import random

from PIL import Image
import pymupdf as fitz
import pytest

from pdf_enhance_core.cancellation import ProcessingCancelled, cancellation_scope
from pdf_enhance_core.pdf_tools import COMPRESSION_PROFILES, PDFToolCleanupError, compress_pdf, remove_pdf_password


def make_pdf(path, encryption=None, user="reader"):
    data = bytes(value & 0xF0 for value in random.Random(0).randbytes(1200 * 900 * 3))
    image = Image.frombytes("RGB", (1200, 900), data)
    pix = fitz.Pixmap(fitz.csRGB, 1200, 900, image.tobytes(), False)
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_image(fitz.Rect(0, 0, 240, 180), pixmap=pix)
        page.insert_text((30, 210), "ALPHA visible text")
        page.insert_text((30, 230), "OCR invisible text", render_mode=3)
        page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(20, 240, 100, 250), "uri": "https://example.org/"})
        doc.set_toc([[1, "ALPHA", 1]])
        doc.set_metadata({"title": "Preserve structure"})
        doc.embfile_add("note.txt", b"attachment")
        widget = fitz.Widget()
        widget.field_name = "name"
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_value = "Ada"
        widget.rect = fitz.Rect(30, 260, 120, 280)
        page.add_widget(widget)
        options = {} if encryption is None else {"encryption": encryption, "user_pw": user,
                                                "owner_pw": "owner", "permissions": fitz.PDF_PERM_PRINT}
        doc.save(path, **options)


def assert_contents(doc):
    assert len(doc) == 1
    assert "ALPHA visible text" in doc[0].get_text()
    assert "OCR invisible text" in doc[0].get_text()
    assert doc.get_toc() == [[1, "ALPHA", 1]]
    assert doc.metadata["title"] == "Preserve structure"
    assert doc.embfile_get("note.txt") == b"attachment"
    assert doc[0].get_links()[0]["uri"] == "https://example.org/"
    assert next(doc[0].widgets()).field_value == "Ada"


@pytest.mark.parametrize("profile", COMPRESSION_PROFILES)
def test_compression_preserves_structure_and_reduces_size(tmp_path, profile):
    source, output = tmp_path / "source.pdf", tmp_path / "compressed.pdf"
    make_pdf(source)
    original = source.read_bytes()
    message = compress_pdf(source, output, profile)
    assert "压缩完成" in message
    assert output.stat().st_size < len(original)
    assert source.read_bytes() == original
    with fitz.open(output) as result, fitz.open(source) as before:
        assert_contents(result)
        assert result[0].rect == before[0].rect
        if profile == COMPRESSION_PROFILES[0]:
            assert result[0].get_pixmap().samples == before[0].get_pixmap().samples
        else:
            old_image = before[0].get_image_info()[0]
            new_image = result[0].get_image_info()[0]
            # MuPDF average subsampling uses integer factors: not every image reaches the target DPI.
            assert new_image["width"] <= old_image["width"]
            assert result.extract_image(result[0].get_images()[0][0])["ext"] == "jpeg"
            if profile == COMPRESSION_PROFILES[2]:
                assert new_image["width"] < old_image["width"]


@pytest.mark.parametrize("encryption", [fitz.PDF_ENCRYPT_RC4_128, fitz.PDF_ENCRYPT_AES_128, fitz.PDF_ENCRYPT_AES_256])
@pytest.mark.parametrize("password", ["reader", "owner"])
def test_remove_known_password(tmp_path, encryption, password):
    source, output = tmp_path / "encrypted.pdf", tmp_path / "unlocked.pdf"
    make_pdf(source, encryption)
    original = source.read_bytes()
    remove_pdf_password(source, output, password)
    assert source.read_bytes() == original
    with fitz.open(output) as result:
        assert not result.needs_pass
        assert result.metadata["encryption"] is None
        assert_contents(result)


def test_compression_keeps_password_and_permissions(tmp_path):
    source, output = tmp_path / "encrypted.pdf", tmp_path / "compressed.pdf"
    make_pdf(source, fitz.PDF_ENCRYPT_AES_256)
    with fitz.open(source) as before:
        before.authenticate("reader")
        permissions = before.permissions
    compress_pdf(source, output, password="reader")
    with fitz.open(output) as result:
        assert result.needs_pass
        assert result.authenticate("reader")
        assert result.permissions == permissions
        assert_contents(result)


def test_empty_user_password_and_wrong_password(tmp_path):
    source, output = tmp_path / "restricted.pdf", tmp_path / "unlocked.pdf"
    make_pdf(source, fitz.PDF_ENCRYPT_AES_256, user="")
    remove_pdf_password(source, output)
    with fitz.open(output) as result:
        assert result.metadata["encryption"] is None
        assert_contents(result)
    original_output = output.read_bytes()
    with pytest.raises(ValueError, match="密码错误"):
        remove_pdf_password(source, output, "wrong")
    assert output.read_bytes() == original_output


@pytest.mark.parametrize("action", [compress_pdf, remove_pdf_password])
def test_cancel_during_save_preserves_existing_output(tmp_path, monkeypatch, action):
    source, output = tmp_path / "encrypted.pdf", tmp_path / "output.pdf"
    make_pdf(source, fitz.PDF_ENCRYPT_AES_256)
    original = source.read_bytes()
    output.write_bytes(b"keep existing output")
    event = threading.Event()
    save = fitz.Document.save
    def cancel_after_save(doc, *args, **kwargs):
        save(doc, *args, **kwargs)
        event.set()
    monkeypatch.setattr(fitz.Document, "save", cancel_after_save)
    with pytest.raises(ProcessingCancelled), cancellation_scope(event):
        action(source, output, password="reader")
    assert source.read_bytes() == original
    assert output.read_bytes() == b"keep existing output"
    assert not list(tmp_path.glob(".pdf-enhance-tools-*"))


def test_no_savings_copies_original_without_quality_loss(tmp_path, monkeypatch):
    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    make_pdf(source)
    original = source.read_bytes()
    save = fitz.Document.save
    def enlarged_save(doc, path, **kwargs):
        save(doc, path, **kwargs)
        with open(path, "ab") as file:
            file.write(b"x" * len(original))
    monkeypatch.setattr(fitz.Document, "save", enlarged_save)
    message = compress_pdf(source, output, COMPRESSION_PROFILES[1])
    assert "没有获得体积收益" in message
    assert output.read_bytes() == original


def test_cleanup_failure_is_visible_even_when_cancelled(tmp_path, monkeypatch):
    import pdf_enhance_core.pdf_tools as tools
    source, output = tmp_path / "encrypted.pdf", tmp_path / "output.pdf"
    make_pdf(source, fitz.PDF_ENCRYPT_AES_256)
    event = threading.Event()
    save = fitz.Document.save
    def cancel_after_save(doc, *args, **kwargs):
        save(doc, *args, **kwargs)
        event.set()
    def fail_remove(*args):
        raise PermissionError("locked temporary file")
    with monkeypatch.context() as patch:
        patch.setattr(fitz.Document, "save", cancel_after_save)
        patch.setattr(tools.os, "remove", fail_remove)
        with pytest.raises(PDFToolCleanupError, match="手动删除"), cancellation_scope(event):
            remove_pdf_password(source, output, "reader")
    assert not output.exists()
    for file in tmp_path.glob(".pdf-enhance-tools-*"):
        file.unlink()


def test_same_file_unsigned_and_signature_guards(tmp_path):
    source = tmp_path / "source.pdf"
    make_pdf(source)
    original = source.read_bytes()
    with pytest.raises(ValueError, match="不能覆盖"):
        compress_pdf(source, source)
    with pytest.raises(ValueError, match="未加密"):
        remove_pdf_password(source, tmp_path / "unlocked.pdf")
    with pytest.raises(ValueError, match="压缩档位"):
        compress_pdf(source, tmp_path / "compressed.pdf", "unknown")
    assert source.read_bytes() == original
    signed = tmp_path / "signature-field.pdf"
    with fitz.open(source) as doc:
        widget = fitz.Widget()
        widget.field_name = "signature"
        widget.field_type = fitz.PDF_WIDGET_TYPE_SIGNATURE
        widget.rect = fitz.Rect(150, 260, 240, 280)
        doc[0].add_widget(widget)
        doc.save(signed)
    with pytest.raises(ValueError, match="签名字段"):
        compress_pdf(signed, tmp_path / "compressed.pdf")
    missing_flags = tmp_path / "signature-without-flags.pdf"
    with fitz.open(signed) as doc:
        doc.xref_set_key(doc.pdf_catalog(), "AcroForm/SigFlags", "0")
        doc.save(missing_flags)
    with pytest.raises(ValueError, match="签名字段"):
        compress_pdf(missing_flags, tmp_path / "compressed.pdf")
